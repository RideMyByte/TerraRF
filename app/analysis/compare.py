"""Compare the propagation model with what was actually received.

Two kinds of ground truth:

* **CoreScope neighbour reports** (``calibrate``): a partner repeater reports the SNR at which it heard an
  advert of our station *directly* (zero hop). The model predicts the link station → partner, using the
  partner's height and antenna: from the project if the partner is a station there, else from the
  per-repeater values the admin entered, else from the defaults the caller passes.
* **Drive tests** (``compare_measurements``, MeshMapper / manual / CSV): the SNR a handheld measured for a
  repeater it heard directly. The model is the station's coverage raster at the measuring point, recomputed
  for each receiver height that occurs (heights come from the measurement, its series or the series'
  placement profile).

Delta = measured − predicted, in dB. Negative means the model is too optimistic.
LoRa radios report SNR only up to roughly +10 dB; where either value reaches that, SNR says nothing about
the link and the row is left out of the SNR statistics (RSSI still counts for drive tests).
"""
from __future__ import annotations

import math

import numpy as np

from ..model import coverage as cov
from ..model import terrain
from ..model.terrain import lonlat_to_merc
from ..site import sysstat
from ..store import catalog

SNR_SAT_DB = 10.0


def noise_floor_dbm(station, settings):
    """Thermal noise in the station's LoRa bandwidth plus receiver noise figure and local noise rise."""
    bandwidth_hz = float(station.get("bw_khz", 125.0)) * 1000.0
    return -174.0 + 10.0 * math.log10(bandwidth_hz) + float(station.get("nf_db", 6.0)) \
        + float(settings.get("noise_rise_db", 0.0))


def delta_stats(deltas):
    """Mean, median, spread and RMSE of a list of deltas (dB); None when empty."""
    if not deltas:
        return None
    d = np.asarray(deltas, dtype=float)
    return {"n": int(d.size), "mean_db": round(float(d.mean()), 1), "median_db": round(float(np.median(d)), 1),
            "std_db": round(float(d.std()), 1), "rmse_db": round(float(np.sqrt((d ** 2).mean())), 1)}


def _station(project, station_id):
    station = next((x for x in project.get("stations") or [] if x["id"] == station_id), None)
    if station is None:
        raise KeyError("station not found")
    return station


# ---------------------------------------------------------------- CoreScope: repeater <-> repeater
def calibrate(project, station_id, partners, rx_height=10.0, rx_gain=3.0, rx_cable=1.0, overrides=None):
    """Directly reported SNR (station -> partner) against the model.

    partners: [{pk, name, lat, lon, snr, snr_n}] – only links with a neighbour report.
    overrides: per-repeater height/gain/cable the admin entered ({pk: {height_m, gain_dbi, cable_db}}).
    """
    overrides = overrides or {}
    settings = cov.merged_settings()
    station = _station(project, station_id)
    stations_by_pk = {x.get("corescope_pk"): x for x in project.get("stations") or [] if x.get("corescope_pk")}
    noise = noise_floor_dbm(station, settings)
    rows = []
    with sysstat.activity(f"Abgleich ({len(partners)} Strecken)"):
        for partner in partners:
            rows.append(_calibrate_one(project, station_id, partner, stations_by_pk, overrides,
                                       rx_height, rx_gain, rx_cable, noise))
    usable = [r["delta_db"] for r in rows if "delta_db" in r and not r["saturated"]]
    return {"station": station.get("name"), "noise_dbm": round(noise, 1), "rx_height_m": rx_height,
            "rx_gain_dbi": rx_gain, "rx_cable_db": rx_cable, "sat_db": SNR_SAT_DB, "rows": rows,
            "stats": delta_stats(usable)}


def _calibrate_one(project, station_id, partner, stations_by_pk, overrides, rx_height, rx_gain, rx_cable, noise):
    row = {"pk": partner.get("pk"), "name": partner.get("name"), "lat": partner["lat"], "lon": partner["lon"],
           "measured_snr": float(partner["snr"]), "n": int(partner.get("snr_n") or 0)}
    as_station = stations_by_pk.get(partner.get("pk"))
    entered = overrides.get(partner.get("pk")) or {}
    try:
        if as_station is not None and as_station["id"] != station_id:
            # the partner is a station of this project: its height, antenna pattern and cable
            row.update(rx_src="station", rx_station_id=as_station["id"])
            prof = cov.profile(project, station_id, 0, 0, rx_station_id=as_station["id"])
        else:
            height = entered.get("height_m", rx_height)
            gain = entered.get("gain_dbi", rx_gain)
            cable = entered.get("cable_db", rx_cable)
            row.update(rx_src="entered" if entered else "standard")
            prof = cov.profile(project, station_id, float(partner["lat"]), float(partner["lon"]), rx_height=float(height),
                               rx_gain=float(gain), rx_cable=float(cable), rx_extra_loss=0.0)
    except (ValueError, KeyError, terrain.SourceError) as e:
        row["error"] = str(e)
        return row
    budget = prof["budget"]
    predicted = budget["prx_dbm"] - noise
    row.update(distance_km=round(prof["distance_m"] / 1000, 2), los_class=prof["los_class"], prx_dbm=budget["prx_dbm"],
               rx_height_m=budget["rx_height_m"], rx_gain_dbi=budget["rx_gain_dbi"], rx_cable_db=budget["rx_cable_db"],
               predicted_snr=round(predicted, 1), delta_db=round(row["measured_snr"] - predicted, 1),
               saturated=row["measured_snr"] >= SNR_SAT_DB or predicted >= SNR_SAT_DB)
    return row


# ---------------------------------------------------------------- drive tests: repeater -> handheld
def receiver_for_measurement(measurement, series, defaults):
    """Receiver height (m), gain (dBi) and cable+extra loss (dB) that applied to one measurement.

    Precedence: value in the measurement > value entered on its series > the series' device/placement
    profile from the catalog > global defaults.
    """
    s = series or {}
    profile = catalog.receiver_for(s.get("device_id"), s.get("placement_id"))
    height = measurement.get("rx_h")
    if height is None:
        height = s.get("rx_h")
    if height is None:
        height = profile["height_m"]
    if height is None:
        height = defaults["rx_height_m"]
    gain = s.get("rx_gain_dbi")
    if gain is None:
        gain = profile["gain_dbi"]
    if gain is None:
        gain = defaults["rx_gain_dbi"]
    loss = s.get("rx_cable_db")
    if loss is None:
        loss = profile["loss_db"]
    if loss is None:
        loss = defaults["rx_cable_db"]
    return round(float(height) * 2) / 2, float(gain), float(loss)


def compare_measurements(project, station_id, measurements, rx_gain=None, series=None):
    """Drive-test measurements of the station's repeater against its computed coverage.

    measurements: rows from ``store.measurements.query`` (snr_down = SNR at the handheld).
    series: {series_id: series row} for receiver details. rx_gain overrides the default receiver gain.
    """
    settings = cov.merged_settings()
    station = _station(project, station_id)
    series = series or {}
    cell_size = cov.project_cs(project, settings)
    noise = noise_floor_dbm(station, settings)
    defaults = {"rx_height_m": float(settings["rx_height_m"]),
                "rx_gain_dbi": float(settings["rx_gain_dbi"]) if rx_gain in (None, "") else float(rx_gain),
                "rx_cable_db": float(settings["rx_cable_db"])}
    rasters = {}   # one coverage raster per receiver height
    sx, sy = lonlat_to_merc(float(station["lon"]), float(station["lat"]))
    out = []
    for m in measurements:
        if m.get("snr_down") is None:
            continue
        height, gain, loss = receiver_for_measurement(m, series.get(m.get("series_id")), defaults)
        if height not in rasters:
            rasters[height] = cov.compute_station(station, {**settings, "rx_height_m": height}, cell_size)[0]
        raster = rasters[height]
        grid = raster.grid
        x, y = lonlat_to_merc(m["lon"], m["lat"])
        col, row = int((x - grid.x0) / grid.cs), int((grid.y1 - y) / grid.cs)
        predicted_snr = predicted_rssi = None
        if 0 <= col < grid.nx and 0 <= row < grid.ny:
            value = float(raster.prx[row, col])
            if not math.isnan(value):
                # the raster already contains the global receiver gain and cable loss -> convert to this receiver
                predicted_rssi = round(value + (gain - float(settings["rx_gain_dbi"]))
                                       - (loss - float(settings["rx_cable_db"])), 1)
                predicted_snr = round(predicted_rssi - noise, 1)
        distance_km = math.hypot(x - sx, y - sy) * math.cos(math.radians(m["lat"])) / 1000
        measured_rssi = m.get("rssi_down")
        out.append({
            "id": m.get("id"), "lat": m["lat"], "lon": m["lon"], "kind": m.get("kind"), "src": m.get("src"),
            "series_id": m.get("series_id"), "rx_height_m": height, "rx_gain_dbi": gain, "rx_cable_db": loss,
            "measured_snr": m["snr_down"], "predicted_snr": predicted_snr, "distance_km": round(distance_km, 2),
            "delta_db": round(m["snr_down"] - predicted_snr, 1) if predicted_snr is not None else None,
            "saturated": predicted_snr is not None and (m["snr_down"] >= SNR_SAT_DB or predicted_snr >= SNR_SAT_DB),
            "outside": predicted_snr is None, "measured_rssi": measured_rssi, "predicted_rssi": predicted_rssi,
            "delta_rssi_db": round(measured_rssi - predicted_rssi, 1)
            if predicted_rssi is not None and measured_rssi is not None else None,
        })
    usable = [o for o in out if o["delta_db"] is not None and not o["saturated"]]
    per_series = {}
    for o in usable:
        per_series.setdefault(o["series_id"], []).append(o["delta_db"])
    return {"station": station.get("name"), "noise_dbm": round(noise, 1), "rx_height_m": defaults["rx_height_m"],
            "rx_gain_dbi": defaults["rx_gain_dbi"], "rx_heights": sorted(rasters), "rows": out,
            "stats": delta_stats([o["delta_db"] for o in usable]),
            "rssi_stats": delta_stats([o["delta_rssi_db"] for o in out if o["delta_rssi_db"] is not None]),
            "series_stats": {str(k): delta_stats(v) for k, v in per_series.items()},
            "saturated": sum(1 for o in out if o["saturated"]), "outside": sum(1 for o in out if o["outside"]),
            "sat_db": SNR_SAT_DB}


def suggest_settings(results):
    """#30: what drive-test comparisons suggest for the global model. Only a suggestion – the admin decides.

    results: outputs of ``compare_measurements`` (not ``calibrate``: repeater links are computed without the
    receiver extra loss, so they cannot calibrate it).

    A systematic offset (median delta, at least 10 usable values) maps onto the receiver extra loss, which
    shifts every prediction equally; a scatter much larger than the offset means the cause is local
    (terrain, buildings) and no global value fixes it.
    """
    deltas = [r["delta_db"] for res in results for r in res.get("rows", [])
              if r.get("delta_db") is not None and not r.get("saturated")]
    if len(deltas) < 10:
        return {"n": len(deltas), "advice": "too_few"}
    median = float(np.median(deltas))
    spread = float(np.std(deltas))
    current = float(cov.merged_settings().get("rx_extra_loss_db", 0.0))
    out = {"n": len(deltas), "median_db": round(median, 1), "std_db": round(spread, 1), "current_extra_loss_db": current}
    if abs(median) < 3:
        out["advice"] = "fits"
    elif spread > 2 * abs(median):
        out["advice"] = "scatter"
    else:
        out["advice"] = "offset"
        out["suggested_extra_loss_db"] = round(max(0.0, current - median), 1)
    return out
