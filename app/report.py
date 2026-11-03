"""Auswertung je Station (Ergebnis-Reiter) und PDF-Bericht zum Vergleich über die Zeit."""
from __future__ import annotations

import io
import math
import time

import numpy as np
from PIL import Image, ImageDraw

from . import corescope, measurements, terrain, tiles
from . import coverage as cov
from . import propagation as prop
from .version import VERSION

RINGS_KM = (1, 2, 3, 5, 7.5, 10, 15, 20, 30, 50, 80)
FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")


def _fonts():
    """DejaVu Sans (Umlaute, Δ, Pfeile, ✓); Rückfall Helvetica."""
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "TRF" in pdfmetrics.getRegisteredFontNames():
        return "TRF", "TRF-B"
    for d in FONT_DIRS:
        r, b = os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf")
        if os.path.exists(r) and os.path.exists(b):
            pdfmetrics.registerFont(TTFont("TRF", r))
            pdfmetrics.registerFont(TTFont("TRF-B", b))
            from reportlab.lib.fonts import addMapping
            addMapping("TRF", 0, 0, "TRF"); addMapping("TRF", 1, 0, "TRF-B")
            return "TRF", "TRF-B"
    return "Helvetica", "Helvetica-Bold"


def _station(project, sid):
    st = next((x for x in project.get("stations") or [] if x["id"] == sid), None)
    if st is None:
        raise KeyError("Station nicht gefunden")
    return st


def _result(project, st):
    s = cov.merged_settings(project)
    cs = cov.project_cs(project, s)
    r, cached = cov.compute_station(st, s, cs)
    return s, r, cached


def summary(project, sid):
    """Kennzahlen einer Station: Fläche je Reserveklasse, Sichtlinie, Versorgung nach Entfernung."""
    st = _station(project, sid)
    s, r, cached = _result(project, st)
    g = r.grid
    kscale = math.cos(math.radians(float(st["lat"])))
    cell_km2 = (g.cs * kscale) ** 2 / 1e6
    sens = prop.sensitivity_dbm(st, s.get("noise_rise_db", 0.0))
    req = float(s["min_margin_db"])
    m = np.asarray(r.prx, dtype=np.float32) - sens
    inside = ~np.isnan(m)
    mm = np.where(inside, m, -999)
    classes = [{"label": "kein Empfang (< 0 dB)", "km2": round(float(((mm < 0) & inside).sum()) * cell_km2, 2)}]
    bins = cov.margin_bins(req)
    for i, (lo, col) in enumerate(bins):
        hi = bins[i + 1][0] if i + 1 < len(bins) else None
        sel = (mm >= lo) & ((mm < hi) if hi is not None else True)
        classes.append({"label": f"{lo:g} … {hi:g} dB" if hi is not None else f"≥ {lo:g} dB",
                        "color": "#%02x%02x%02x" % tuple(col[:3]), "km2": round(float(sel.sum()) * cell_km2, 2)})
    total = float(inside.sum()) * cell_km2
    covered = float((mm >= req).sum()) * cell_km2
    los = float((np.nan_to_num(r.vmain, nan=1e9) <= 0).sum()) * cell_km2
    # Versorgung nach Entfernung (Anteil der Fläche je Ring mit Reserve ≥ Mindestreserve)
    x0, y0 = terrain.lonlat_to_merc(float(st["lon"]), float(st["lat"]))
    cols = (np.arange(g.nx) + 0.5) * g.cs + g.x0
    rows = g.y1 - (np.arange(g.ny) + 0.5) * g.cs
    dist = np.hypot(((cols - x0) * kscale)[None, :], ((rows - y0) * kscale)[:, None]) / 1000
    rings, lo = [], 0.0
    rmax = float(st.get("radius_km", 10))
    for hi in RINGS_KM:
        if lo >= rmax:
            break
        hi = min(hi, rmax)
        sel = inside & (dist >= lo) & (dist < hi)
        n = int(sel.sum())
        if n:
            rings.append({"from_km": lo, "to_km": hi, "covered_pct": round(100 * float((mm[sel] >= req).mean()), 1),
                          "median_margin_db": round(float(np.median(m[sel])), 1)})
        lo = hi
    a = st.get("antenna") or {}
    eirp = float(st["tx_power_dbm"]) - float(st.get("cable_loss_db", 0) or 0) + float(a.get("gain_dbi", 0) or 0)
    return {
        "station": st.get("name"), "id": sid, "cached": cached, "sensitivity_dbm": round(sens, 1), "eirp_dbm": round(eirp, 1),
        "min_margin_db": req, "area_total_km2": round(total, 2), "area_covered_km2": round(covered, 2),
        "covered_pct": round(100 * covered / total, 1) if total else 0, "area_los_km2": round(los, 2),
        "classes": classes, "rings": rings, "info": r.info, "key": r.key,
        "margin_p50_db": round(float(np.median(m[inside])), 1) if inside.any() else None,
        "margin_p90_db": round(float(np.percentile(m[inside], 90)), 1) if inside.any() else None,
    }


# ---------------------------------------------------------------- Kartenbild
def _map_image(st, r, s, width=1500, neighbors=None, points=None):
    g = r.grid
    l, b, rr, t = g.bounds
    span = max(rr - l, t - b)
    z = max(3, min(16, int(math.floor(math.log2(width / 256 * 2 * tiles_world() / span)))))
    n = 2 ** z
    W = 2 * tiles_world()
    tx0 = int((l + tiles_world()) / W * n); tx1 = int((rr + tiles_world()) / W * n)
    ty0 = int((tiles_world() - t) / W * n); ty1 = int((tiles_world() - b) / W * n)
    img = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (236, 238, 241))
    base_ok = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            try:
                tile = Image.open(io.BytesIO(tiles.xyz("osm", z, tx, ty))).convert("L").convert("RGB")
                img.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
                base_ok += 1
            except Exception:  # noqa: BLE001 – ohne Grundkarte weiter
                pass
    px = 256 * n / W  # Pixel je Mercator-Meter

    def to_px(x, y):
        return (x + tiles_world()) * px - tx0 * 256, (tiles_world() - y) * px - ty0 * 256

    # Abdeckung: Reserveklassen
    sens = prop.sensitivity_dbm(st, s.get("noise_rise_db", 0.0))
    m = np.asarray(r.prx, dtype=np.float32) - sens
    rgba = np.zeros(m.shape + (4,), np.uint8)
    bins = cov.margin_bins(float(s["min_margin_db"]))
    mm = np.nan_to_num(m, nan=-999)
    for i, (lo, col) in enumerate(bins):
        hi = bins[i + 1][0] if i + 1 < len(bins) else 1e9
        sel = (mm >= lo) & (mm < hi)
        rgba[sel] = (*col[:3], 165)
    ov = Image.fromarray(rgba, "RGBA")
    X0, Y0 = to_px(l, t)
    X1, Y1 = to_px(rr, b)
    ov = ov.resize((max(1, int(round(X1 - X0))), max(1, int(round(Y1 - Y0)))), Image.NEAREST)
    img = img.convert("RGBA")
    img.alpha_composite(ov, (int(round(X0)), int(round(Y0))))
    d = ImageDraw.Draw(img)
    for nb in neighbors or []:
        nx_, ny_ = to_px(*terrain.lonlat_to_merc(nb["lon"], nb["lat"]))
        sn = nb.get("snr") if nb.get("snr") is not None else nb.get("snr_any")
        col = (22, 163, 74) if sn is not None and sn >= 5 else (101, 163, 13) if sn is not None and sn >= -5 \
            else (217, 119, 6) if sn is not None and sn >= -10 else (220, 38, 38) if sn is not None else (156, 163, 175)
        d.ellipse([nx_ - 5, ny_ - 5, nx_ + 5, ny_ + 5], fill=col, outline=(17, 24, 39) if nb.get("snr") is not None else (255, 255, 255), width=2)
    for pt in points or []:  # Messpunkte, Farbe nach gemessenem SNR
        px_, py_ = to_px(*terrain.lonlat_to_merc(pt["lon"], pt["lat"]))
        sn = pt.get("snr_down")
        col = (22, 163, 74) if sn >= 5 else (101, 163, 13) if sn >= -5 else (217, 119, 6) if sn >= -10 else (220, 38, 38)
        d.rectangle([px_ - 3, py_ - 3, px_ + 3, py_ + 3], fill=col, outline=(255, 255, 255))
    sx, sy = to_px(*terrain.lonlat_to_merc(float(st["lon"]), float(st["lat"])))
    d.ellipse([sx - 8, sy - 8, sx + 8, sy + 8], fill=(220, 38, 38), outline=(255, 255, 255), width=3)
    img = img.crop((int(max(0, X0 - 20)), int(max(0, Y0 - 20)), int(min(img.width, X1 + 20)), int(min(img.height, Y1 + 20))))
    if img.width > width:
        img = img.resize((width, int(img.height * width / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue(), base_ok > 0


def tiles_world():
    return 20037508.342789244


# ---------------------------------------------------------------- PDF
def pdf(project, sid, cal_defaults=None, site=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

    st = _station(project, sid)
    sm = summary(project, sid)
    s, r, _ = _result(project, st)
    a = st.get("antenna") or {}
    ss = getSampleStyleSheet()
    FR, FB = _fonts()
    H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName=FB, fontSize=17, spaceAfter=4)
    H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=FB, fontSize=12.5, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#0d2236"))
    P = ParagraphStyle("p", parent=ss["BodyText"], fontName=FR, fontSize=9, leading=12)
    SM = ParagraphStyle("sm", parent=P, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#6b7280"))

    def table(rows, widths, head=True, zebra=True):
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        style = [("FONTNAME", (0, 0), (-1, -1), FR), ("FONTSIZE", (0, 0), (-1, -1), 8.3), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("TOPPADDING", (0, 0), (-1, -1), 2.5)]
        if head:
            style += [("FONTNAME", (0, 0), (-1, 0), FB), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#9ca3af"))]
        if zebra:
            for i in range(1 if head else 0, len(rows)):
                if i % 2 == 0:
                    style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f3f4f6")))
        t.setStyle(TableStyle(style))
        return t

    f = lambda v, d=1: "–" if v is None else f"{v:.{d}f}".replace(".", ",")  # noqa: E731
    story = []
    stamp = time.strftime("%d.%m.%Y %H:%M")
    story.append(Paragraph(f"Stationsbericht: {_e(st.get('name'))}", H1))
    story.append(Paragraph(f"Projekt „{_e(project.get('name'))}“ · erstellt {stamp} · TerraRF {VERSION} · Modell {cov.MODEL_VERSION}"
                           + (f" · {_e(site)}" if site else ""), SM))
    story.append(Spacer(1, 4 * mm))

    # Karte
    nbs = None
    mc = None
    if st.get("corescope_pk") and corescope.enabled():
        try:
            mc = corescope.links(str(st["corescope_pk"]).lower())
            nbs = [x for x in mc["links"] if x.get("bidir") or x.get("snr") is not None]
        except Exception:  # noqa: BLE001
            mc = None
    mrows = measurements.query(None, st["corescope_pk"]) if st.get("corescope_pk") else []
    mrows = [m for m in mrows if m.get("snr_down") is not None]
    try:
        jpg, base_ok = _map_image(st, r, s, neighbors=nbs, points=mrows)
        im = RLImage(io.BytesIO(jpg))
        ratio = im.imageHeight / im.imageWidth
        im.drawWidth = 180 * mm
        im.drawHeight = min(150 * mm, 180 * mm * ratio)
        im.drawWidth = im.drawHeight / ratio
        story.append(im)
        story.append(Paragraph("Bester Pegel als Reserve über der Empfindlichkeit (Farben wie in der Karte)."
                               + ("" if base_ok else " Grundkarte nicht verfügbar.")
                               + (" Punkte: MeshCore-Nachbarn, Farbe nach SNR (dunkler Rand = direkt gemeldet)." if nbs else "")
                               + (" Quadrate: Messfahrten, Farbe nach gemessenem SNR." if mrows else "")
                               + " Kartendaten © OpenStreetMap-Mitwirkende.", SM))
    except Exception as e:  # noqa: BLE001
        story.append(Paragraph(f"Kartenbild konnte nicht erzeugt werden: {_e(e)}", SM))

    # Station
    story.append(Paragraph("Station", H2))
    ant = (f"Richtantenne {f(a.get('gain_dbi'))} dBi, Azimut {f(a.get('azimuth_deg') or 0, 0)}°, "
           f"Öffnung {f(a.get('hbw_deg') or 60, 0)}°/{f(a.get('vbw_deg') or prop.auto_vbw(a), 0)}°, Neigung {f(a.get('tilt_deg') or 0)}°") \
        if a.get("type") == "directional" else f"Rundstrahler {f(a.get('gain_dbi'))} dBi, Neigung {f(a.get('tilt_deg') or 0)}°"
    info = sm["info"]
    rows = [["Position", f"{float(st['lat']):.6f}, {float(st['lon']):.6f}"],
            ["Antennenhöhe", f"{f(float(st.get('height_m', 10)))} m über {'Dach/Oberfläche' if st.get('height_ref') == 'surface' else 'Gelände'}"
             f" (Gelände {f(info.get('ground_m'))} m, Antenne {f(info.get('antenna_alt_m'))} m ü. NHN)"],
            ["Antenne", ant],
            ["Funk", f"{f(float(st['freq_mhz']), 3)} MHz, {f(float(st.get('bw_khz', 125)))} kHz"
             + (f", SF{st.get('sf')}" if st.get("mode") == "lora" else "") + f", Rauschzahl {f(float(st.get('nf_db', 6)))} dB"],
            ["Leistung", f"{f(float(st['tx_power_dbm']))} dBm, Kabel {f(float(st.get('cable_loss_db', 0) or 0))} dB, EIRP {f(sm['eirp_dbm'])} dBm"],
            ["Empfindlichkeit", f"{f(sm['sensitivity_dbm'])} dBm"],
            ["Rechenradius", f"{f(float(st.get('radius_km', 10)))} km"]]
    if st.get("notes"):
        rows.append(["Notizen", Paragraph(_e(st["notes"]), P)])
    story.append(table(rows, [32 * mm, 148 * mm], head=False))

    # Ergebnis
    story.append(Paragraph("Ergebnis", H2))
    story.append(Paragraph(
        f"Versorgt (Reserve ≥ {f(sm['min_margin_db'], 0)} dB): <b>{f(sm['area_covered_km2'])} km²</b> "
        f"= {f(sm['covered_pct'])} % der berechneten Fläche ({f(sm['area_total_km2'])} km²). "
        f"Sichtlinie: {f(sm['area_los_km2'])} km². Reserve im Median {f(sm['margin_p50_db'])} dB, "
        f"90 %-Wert {f(sm['margin_p90_db'])} dB.", P))
    story.append(Spacer(1, 2 * mm))
    crow = [["Reserveklasse", "Fläche km²", "Anteil"]] + [
        [c["label"], f(c["km2"]), f"{f(100 * c['km2'] / sm['area_total_km2'] if sm['area_total_km2'] else 0)} %"] for c in sm["classes"]]
    rrow = [["Entfernung", "versorgt", "Median-Reserve"]] + [
        [f"{f(x['from_km'], 1)} – {f(x['to_km'], 1)} km", f"{f(x['covered_pct'])} %", f"{f(x['median_margin_db'])} dB"] for x in sm["rings"]]
    t = Table([[table(crow, [36 * mm, 20 * mm, 18 * mm]), table(rrow, [30 * mm, 18 * mm, 26 * mm])]], colWidths=[88 * mm, 92 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(t)
    if info.get("warnings"):
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("Hinweise: " + _e("; ".join(info["warnings"])), SM))

    # Modell
    story.append(Paragraph("Modell und Daten", H2))
    names = {x["id"]: x["label"] for x in terrain.list_sources()}
    mrows = [["Gelände / Oberfläche", f"{names.get(s['ground_source'], s['ground_source'])} / {names.get(s.get('surface_source'), s.get('surface_source'))}"],
             ["Ersatz außerhalb", names.get(s.get("fallback_source"), s.get("fallback_source") or "keine")],
             ["Auflösung", f"{f(float(s['res_m']), 0)} m, {info.get('rays')} Strahlen, Schritt {f(info.get('step_m'))} m"],
             ["Ausbreitung", f"Beugung {('Deygout' if s.get('diffraction_model') == 'deygout' else 'Bullington')}, Clutter {s.get('clutter_mode')}, "
              f"Hindernisse (P.833) {s.get('veg_mode')}, Bodenreflexion {'an' if s.get('two_ray', True) else 'aus'}, k = {f(float(s['k_factor']), 2)}"],
             ["Empfänger", f"{f(float(s['rx_height_m']))} m, {f(float(s['rx_gain_dbi']))} dBi, Kabel {f(float(s['rx_cable_db']))} dB, "
              f"Zusatz {f(float(s.get('rx_extra_loss_db', 0)))} dB, Rauschanhebung {f(float(s.get('noise_rise_db', 0)))} dB"]]
    story.append(table(mrows, [36 * mm, 144 * mm], head=False))

    # Messfahrten
    if mrows:
        story.append(Paragraph("Messfahrten ↔ Modell", H2))
        try:
            cmp_ = cov.compare_measurements(project, sid, mrows)
            stt = cmp_.get("stats")
            kinds = {}
            for m in mrows:
                kinds[m.get("kind") or "?"] = kinds.get(m.get("kind") or "?", 0) + 1
            first = min(m["ts"] for m in mrows if m.get("ts")); last = max(m["ts"] for m in mrows if m.get("ts"))
            story.append(Paragraph(
                f"{len(mrows)} Messungen dieses Repeaters ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}), "
                f"{time.strftime('%d.%m.%Y', time.localtime(first))} bis {time.strftime('%d.%m.%Y', time.localtime(last))}. "
                f"Modell mit Empfänger {f(cmp_['rx_height_m'])} m / {f(cmp_['rx_gain_dbi'])} dBi, Rauschboden {f(cmp_['noise_dbm'])} dBm"
                + (f"; {cmp_['outside']} Messungen außerhalb des Rechenradius." if cmp_['outside'] else "."), P))
            if stt:
                story.append(Paragraph(
                    f"Δ = gemessen − Modell: Mittel <b>{'+' if stt['mean_db'] > 0 else ''}{f(stt['mean_db'])} dB</b>, Median {f(stt['median_db'])} dB, "
                    f"Streuung {f(stt['std_db'])} dB, RMSE {f(stt['rmse_db'])} dB, n = {stt['n']}. Negativ heißt: das Modell ist zu optimistisch.", P))
            sr = cmp_.get("rssi_stats")
            if sr:
                story.append(Paragraph(f"RSSI: Δ Mittel {'+' if sr['mean_db'] > 0 else ''}{f(sr['mean_db'])} dB, Median {f(sr['median_db'])} dB, "
                                       f"Streuung {f(sr['std_db'])} dB, n = {sr['n']}.", P))
            if cmp_.get("saturated"):
                story.append(Paragraph(f"{cmp_['saturated']} Messungen nahe am Repeater nicht in der SNR-Statistik "
                                       f"(LoRa meldet höchstens etwa {f(cmp_['sat_db'], 0)} dB SNR).", SM))
            # nach Entfernung
            rows_ = [x for x in cmp_["rows"] if x["delta_db"] is not None and not x["saturated"]]
            if rows_:
                tab = [["Entfernung", "n", "gemessen (Median)", "Modell (Median)", "Δ (Median)"]]
                lo = 0.0
                for hi in RINGS_KM:
                    sel = [x for x in rows_ if lo <= x["distance_km"] < hi]
                    if sel:
                        tab.append([f"{f(lo, 1)} – {f(hi, 1)} km", str(len(sel)), f(float(np.median([x['measured_snr'] for x in sel]))),
                                    f(float(np.median([x['predicted_snr'] for x in sel]))), f(float(np.median([x['delta_db'] for x in sel])))])
                    lo = hi
                story.append(Spacer(1, 2 * mm))
                story.append(table(tab, [34 * mm, 14 * mm, 38 * mm, 34 * mm, 28 * mm]))
            story.append(Paragraph("Vergleich mit der berechneten Abdeckung am Messort (Empfänger-Einstellungen des Projekts). "
                                   "Gemessen wird das SNR beim Messgerät (Repeater → Messgerät).", SM))
        except Exception as e:  # noqa: BLE001
            story.append(Paragraph(f"Vergleich nicht möglich: {_e(e)}", SM))

    # MeshCore
    if mc:
        story.append(Paragraph("MeshCore", H2))
        meta = None
        try:
            meta = corescope.node_meta(str(st["corescope_pk"]).lower())
        except Exception:  # noqa: BLE001
            pass
        nm = (meta or {}).get("node") or {}
        bits = [f"Knoten <b>{_e(mc.get('name') or nm.get('name') or st['corescope_pk'][:8])}</b>"]
        if nm.get("last_seen"):
            bits.append(f"zuletzt gehört {_e(nm['last_seen'].replace('T', ' ')[:16])} UTC")
        if nm.get("battery_mv"):
            bits.append(f"Akku {nm['battery_mv'] / 1000:.2f} V".replace(".", ","))
        bits.append(f"{len(mc['links'])} bekannte Verbindungen, {sum(1 for x in mc['links'] if x.get('bidir'))} beidseitig, "
                    f"{sum(1 for x in mc['links'] if x.get('snr') is not None)} mit gemessenem SNR (Fenster {mc.get('window_days') or '?'} Tage)")
        story.append(Paragraph(" · ".join(bits) + f". Quelle: {_e(corescope.BASE)}", P))
        nb = sorted([x for x in mc["links"] if x.get("bidir") or x.get("snr") is not None],
                    key=lambda x: -(x["snr"] if x.get("snr") is not None else (x.get("snr_any") if x.get("snr_any") is not None else -99)))[:30]
        if nb:
            story.append(Spacer(1, 2 * mm))
            story.append(table([["Nachbar", "km", "Tx →", "Rx ←", "SNR dB"]] + [
                [Paragraph(_e(x.get("name") or x["pk"][:8]), P), f(x.get("distance_km")), str(x.get("they_hear", 0)), str(x.get("we_hear", 0)),
                 (f(x["snr"]) + " direkt") if x.get("snr") is not None else (f(x.get("snr_any")) + " Ø" if x.get("snr_any") is not None else "–")]
                for x in nb], [80 * mm, 18 * mm, 20 * mm, 20 * mm, 30 * mm]))
            story.append(Paragraph("Tx: wie oft der Nachbar diese Station gehört hat; Rx: umgekehrt. „direkt“ = aus der Nachbartabelle, "
                                   "die der Partner selbst meldet (direkt gehörtes Advert); „Ø“ = aus Paketpfaden abgeleitet, unsicher. "
                                   "Höchstens 30 Einträge.", SM))
        pts = [x for x in mc["links"] if x.get("snr") is not None]
        if pts:
            cd = cal_defaults or {}
            try:
                res = cov.calibrate(project, sid, pts, float(cd.get("rx_height_m", 10)), float(cd.get("rx_gain_dbi", 3)),
                                    float(cd.get("rx_cable_db", 1)), corescope.overrides())
                rows = [["Partner", "Höhe", "km", "LOS", "gemessen", "Modell", "Δ"]]
                for x in res["rows"]:
                    if "delta_db" not in x:
                        rows.append([Paragraph(_e(x.get("name")), P), "", "", "", f(x["measured_snr"]), "Fehler", ""])
                        continue
                    rows.append([Paragraph(_e(x.get("name")) + (" *" if x["saturated"] else ""), P), f"{f(x['rx_height_m'], 0)} m",
                                 f(x["distance_km"]), ["–", "~", "✓"][x["los_class"]], f(x["measured_snr"]), f(x["predicted_snr"]),
                                 ("+" if x["delta_db"] > 0 else "") + f(x["delta_db"])])
                story.append(KeepTogether([Paragraph("Abgleich Modell ↔ Messung", H2), table(rows, [60 * mm, 16 * mm, 16 * mm, 12 * mm, 24 * mm, 24 * mm, 20 * mm])]))
                stt = res.get("stats")
                if stt:
                    story.append(Paragraph(
                        f"Δ = gemessen − Modell: Mittel <b>{'+' if stt['mean_db'] > 0 else ''}{f(stt['mean_db'])} dB</b>, Median {f(stt['median_db'])} dB, "
                        f"Streuung {f(stt['std_db'])} dB, n = {stt['n']}. Negativ heißt: das Modell ist zu optimistisch.", P))
                story.append(Paragraph(f"Rauschboden {f(res['noise_dbm'])} dBm. * SNR ≥ {f(res['sat_db'], 0)} dB nicht in der Statistik "
                                       "(LoRa-Meldungen sind nach oben begrenzt). Gegenstelle: Werte der Station im Projekt, sonst eigene "
                                       "Angabe zum Repeater, sonst Standardwerte.", SM))
            except Exception as e:  # noqa: BLE001
                story.append(Paragraph(f"Abgleich nicht möglich: {_e(e)}", SM))

    def footer(c, doc):
        c.saveState()
        c.setFont(FR, 7.5)
        c.setFillColor(colors.HexColor("#6b7280"))
        c.drawString(15 * mm, 10 * mm, f"TerraRF {VERSION} · {st.get('name')} · {stamp}")
        c.drawRightString(195 * mm, 10 * mm, f"Seite {doc.page}")
        c.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"TerraRF – {st.get('name')}", author="TerraRF")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


_EMOJI = None


def _e(s):
    """Für Paragraph-Text: escapen und Zeichen entfernen, die keine PDF-Schrift hat (Emoji in Knotennamen)."""
    global _EMOJI
    import re
    if _EMOJI is None:
        _EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE00-\uFE0F\u200d]")
    t = _EMOJI.sub("", str(s if s is not None else "")).strip()
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
