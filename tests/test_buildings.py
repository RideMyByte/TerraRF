"""#23/#25: Gebäude direkt an der Station und auf kurzen Strecken; Abstrahlsektor."""
import math
import os

import numpy as np
import pytest
import rasterio
from rasterio.warp import transform as _warp
from rasterio.transform import from_origin

from app.model import coverage as cov
from app.model.terrain import lonlat_to_merc

X0, Y1, RES, N = 360000.0, 5718000.0, 5.0, 800   # 4 km, 5 m
CX, CY = X0 + 2000, Y1 - 2000


def _to_ll(x, y):
    lon, lat = _warp("EPSG:25832", "EPSG:4326", [x], [y])
    return lon[0], lat[0]


@pytest.fixture(scope="module")
def bldg(terrain):
    g = np.full((N, N), 50.0)
    s = g.copy()
    c0, c1 = int((CX + 5 - X0) / RES), int((CX + 25 - X0) / RES)      # 20 m tief, 5 m östlich der Antenne
    r0, r1 = int((Y1 - (CY + 20)) / RES), int((Y1 - (CY - 20)) / RES)  # 40 m breit
    s[r0:r1, c0:c1] += 30
    for name, arr in (("bg", g), ("bs", s)):
        d = os.path.join(terrain, "dem", name)
        os.makedirs(d, exist_ok=True)
        with rasterio.open(os.path.join(d, name + ".tif"), "w", driver="GTiff", width=N, height=N, count=1, dtype="float32",
                           crs="EPSG:25832", transform=from_origin(X0, Y1, RES, RES), nodata=-9999) as ds:
            ds.write(arr.astype(np.float32), 1)
    lon, lat = _to_ll(CX, CY)
    return lat, lon


from app.model import settings as settings_store


def _settings(res):
    return {"ground_source": "local:bg", "surface_source": "local:bs", "res_m": res, "fallback_source": "none"}


def _proj(lat, lon, res, ant=None):
    st = {"id": "a", "name": "A", "enabled": True, "lat": lat, "lon": lon, "height_m": 10, "height_ref": "ground",
          "tx_power_dbm": 22, "freq_mhz": 869.618, "bw_khz": 62.5, "mode": "lora", "sf": 8, "nf_db": 6, "cable_loss_db": 0,
          "radius_km": 1.2, "antenna": ant or {"type": "omni", "gain_dbi": 2}}
    return {"stations": [st], "_res": res}


def _at(dx, dy):
    lon, lat = _to_ll(CX + dx, CY + dy)
    return lat, lon


def _raster(pj, dx, dy):
    with settings_store.temporary(_settings(pj["_res"])):
        s = cov.merged_settings(pj)
        r, _ = cov.compute_station(pj["stations"][0], s, cov.project_cs(pj, s))
    lat, lon = _at(dx, dy)
    x, y = lonlat_to_merc(lon, lat)
    g = r.grid
    return float(r.prx[int((g.y1 - y) / g.cs), int((x - g.x0) / g.cs)])


def _profile(pj, lat, lon):
    with settings_store.temporary(_settings(pj["_res"])):
        return cov.profile(pj, "a", lat, lon)


@pytest.mark.parametrize("res", [10, 25])
def test_building_next_to_station_shadows_one_side(bldg, res):
    pj = _proj(*bldg, res)
    east, west = _raster(pj, 300, 0), _raster(pj, -300, 0)
    assert west - east > 40, (west, east)            # vorher bei 25 m kaum Unterschied bzw. 360° verschattet
    p = _profile(pj, *_at(300, 0))["budget"]
    assert abs(p["prx_dbm"] - east) < 4               # Profil und Fläche stimmen überein
    assert _profile(pj, *_at(-300, 0))["budget"]["diffraction_db"] < 1   # Rückseite frei


def test_short_path_behind_building_is_diffracted(bldg):
    p = _profile(_proj(*bldg, 10), *_at(60, 0))["budget"]   # ganze Strecke im Empfänger-Nahbereich (60 m)
    assert p["diffraction_db"] > 30, p


def test_sector_attenuates_outside(bldg):
    lat, lon = bldg
    ant = {"type": "omni", "gain_dbi": 2, "sector_on": True, "sector_az_deg": 0, "sector_width_deg": 180, "sector_loss_db": 25}
    pj0, pj1 = _proj(lat, lon, 25), _proj(lat, lon, 25, ant)
    assert abs(_raster(pj1, 0, 400) - _raster(pj0, 0, 400)) < 0.5          # Norden: im Sektor
    assert _raster(pj0, 0, -400) - _raster(pj1, 0, -400) > 24               # Süden: außerhalb


def test_mast_height_from_roof(bldg):
    """#43: a repeater on (or right next to) a 30 m building gets 30 m + 1 m; in the open no guess."""
    with settings_store.temporary(_settings(10)):
        on_roof = cov.mast_height_guess({}, *_at(15, 0))
        beside = cov.mast_height_guess({}, *_at(0, 0))       # 5 m west of the building: still within 8 m
        open_field = cov.mast_height_guess({}, *_at(-300, 300))
    assert on_roof == pytest.approx(31.0, abs=0.2)
    assert beside == pytest.approx(31.0, abs=0.2)
    assert open_field is None


def test_roof_under_measurement_point(bldg):
    """#56: a point in the middle of the 30 m building is on its roof; beside it (street, GPS error) and in the
    open the default stays."""
    with settings_store.temporary(_settings(10)):
        assert cov.roof_height({}, *_at(15, 0)) == pytest.approx(30.0, abs=0.2)
        assert cov.roof_height({}, *_at(0, 0)) is None          # 5 m beside the wall
        assert cov.roof_height({}, *_at(-300, 300)) is None


def test_roofs_need_a_footprint_when_footprints_exist(bldg):
    """#56: with footprints loaded, a tall object without footprint (a tree) is no roof and no mast position."""
    from app.model import buildings
    ring = [list(_to_ll(x, y)) for x, y in ((CX + 300, CY + 300), (CX + 320, CY + 300), (CX + 320, CY + 320),
                                             (CX + 300, CY + 320), (CX + 300, CY + 300))]
    buildings.add([{"id": "way/9", "rings": [ring], "props": {}}], "test")
    try:
        with settings_store.temporary(_settings(10)):
            assert cov.roof_height({}, *_at(15, 0)) is None          # the DOM block has no footprint here
            assert cov.mast_height_guess({}, *_at(15, 0)) is None
    finally:
        buildings.clear()
    with settings_store.temporary(_settings(10)):
        assert cov.roof_height({}, *_at(15, 0)) == pytest.approx(30.0, abs=0.2)


def test_receiver_on_roof_in_comparison():
    from app.analysis import compare
    defaults = {"rx_height_m": 1.5, "rx_gain_dbi": 2.0, "rx_cable_db": 0.0}
    assert compare.receiver_for_measurement({}, None, defaults, roof=30.0)[0] == 31.5
    assert compare.receiver_for_measurement({"rx_h": 2}, None, defaults, roof=30.0)[0] == 2
    assert compare.receiver_for_measurement({}, None, defaults)[0] == 1.5
