"""Propagation kernels.

Path loss per receiver location:

    L = FSPL(d3D)
      + ground reflection (two-ray / plane-earth term, line of sight only)
      + diffraction       (Bullington per ITU-R P.526 or Deygout, over ground + surface)
      + obstacle loss     (ITU-R P.833 where the direct ray runs through objects of the surface model)
      + terminal clutter  (ITU-R P.2108 §3.1 height-gain terminal correction at the receiver)

* Effective earth radius k * 6371 km (default k = 4/3), applied as earth bulge.
* To avoid double counting, obstacles within `excl_m` of the receiver are taken
  out of the diffraction profile when the clutter model is on – the P.2108 term
  replaces them. Their height (surface minus ground) is the representative
  clutter height R in "auto" mode.
* Antenna pattern: horizontal + vertical parabolic (3GPP-style) attenuation,
  limited by the front-to-back ratio.

Coverage is computed along radial rays from the transmitter (like SPLAT!).
Obstacles inside each radial bin are max-pooled.
"""
from __future__ import annotations

import math

import numpy as np
from numba import njit, prange

C = 299792458.0

# clutter classes: (R m, model) model 0 = height gain, 1 = diffraction (P.2108 Table 3)
CLUTTER_CLASSES = {
    "none": (0.0, 0),
    "suburban": (10.0, 0),   # suburban
    "urban": (15.0, 1),      # urban
    "trees": (15.0, 1),      # trees / forest
    "dense": (20.0, 1),      # dense urban
}


@njit(cache=True, fastmath=True)
def knife_edge_loss(v):
    if v <= -0.78:
        return 0.0
    t = v - 0.1
    return 6.9 + 20.0 * math.log10(math.sqrt(t * t + 1.0) + t)


@njit(cache=True, fastmath=True)
def p2108_terminal(h, R, f_ghz, typ):
    """ITU-R P.2108 §3.1 height gain terminal correction (dB), terminal height h
    below representative clutter height R. typ 0: height-gain form, 1: diffraction form."""
    if R <= 0.0 or h >= R:
        return 0.0
    if typ == 0:
        kh2 = 21.8 + 6.2 * math.log10(f_ghz)
        return -kh2 * math.log10(h / R)
    hdif = R - h
    theta = math.degrees(math.atan(hdif / 27.0))
    knu = 0.342 * math.sqrt(f_ghz)
    nu = knu * math.sqrt(hdif * theta)
    return knife_edge_loss(nu) - 6.03


@njit(cache=True, fastmath=True)
def sample(arr, fx, fy):
    ny, nx = arr.shape
    if fx < 0.0:
        fx = 0.0
    if fy < 0.0:
        fy = 0.0
    if fx > nx - 1.001:
        fx = nx - 1.001
    if fy > ny - 1.001:
        fy = ny - 1.001
    ix = int(fx)
    iy = int(fy)
    dx = fx - ix
    dy = fy - iy
    return (arr[iy, ix] * (1 - dx) * (1 - dy) + arr[iy, ix + 1] * dx * (1 - dy)
            + arr[iy + 1, ix] * (1 - dx) * dy + arr[iy + 1, ix + 1] * dx * dy)


@njit(cache=True, fastmath=True)
def _max_v(d, z, i0, i1, za, zb, lam):
    da = d[i0]
    L = d[i1] - da
    best = -1e9
    bi = -1
    if L <= 0:
        return best, bi
    for i in range(i0 + 1, i1):
        d1 = d[i] - da
        d2 = d[i1] - d[i]
        if d1 <= 0.0 or d2 <= 0.0:
            continue
        h = z[i] - (za + (zb - za) * d1 / L)
        v = h * math.sqrt(2.0 * L / (lam * d1 * d2))
        if v > best:
            best = v
            bi = i
    return best, bi


@njit(cache=True, fastmath=True)
def deygout(d, z, j, ztx, zrx, lam):
    v1, m = _max_v(d, z, 0, j, ztx, zrx, lam)
    if m < 0:
        return 0.0, -1e9
    loss = knife_edge_loss(v1)
    if v1 > -0.78:
        v2, m2 = _max_v(d, z, 0, m, ztx, z[m], lam)
        if m2 >= 0:
            loss += knife_edge_loss(v2)
        v3, m3 = _max_v(d, z, m, j, z[m], zrx, lam)
        if m3 >= 0:
            loss += knife_edge_loss(v3)
    return loss, v1


@njit(cache=True, fastmath=True)
def bullington(d, z, j, ztx, zrx, lam):
    """ITU-R P.526 Bullington equivalent knife edge for path 0..j."""
    D = d[j]
    if j < 2 or D <= 0:
        return 0.0, -1e9
    str_ = (zrx - ztx) / D
    stim = -1e18
    vmax = -1e9
    for i in range(1, j):
        di = d[i]
        s_ = (z[i] - ztx) / di
        if s_ > stim:
            stim = s_
        d2 = D - di
        h = z[i] - (ztx * d2 + zrx * di) / D
        v = h * math.sqrt(2.0 * D / (lam * di * d2))
        if v > vmax:
            vmax = v
    if stim < str_:
        return knife_edge_loss(vmax), vmax
    srim = -1e18
    for i in range(1, j):
        s_ = (z[i] - zrx) / (D - d[i])
        if s_ > srim:
            srim = s_
    db = (zrx - ztx + srim * D) / (stim + srim)
    if db <= 0.0 or db >= D:
        return knife_edge_loss(vmax), vmax
    hb = ztx + stim * db - (ztx * (D - db) + zrx * db) / D
    vb = hb * math.sqrt(2.0 * D / (lam * db * (D - db)))
    return knife_edge_loss(vb), max(vb, vmax if vmax > 0 else vb)


@njit(cache=True, fastmath=True)
def diffraction(d, z, j, ztx, zrx, lam, model):
    if model == 1:
        return deygout(d, z, j, ztx, zrx, lam)
    return bullington(d, z, j, ztx, zrx, lam)


@njit(cache=True)
def mask_at(mask, fx, fy):
    """Nearest-cell lookup in a 0/1 mask (building footprints); 0 outside."""
    ny, nx = mask.shape
    ix = int(fx + 0.5)
    iy = int(fy + 0.5)
    if ix < 0 or iy < 0 or ix >= nx or iy >= ny:
        return 0
    return mask[iy, ix]


@njit(cache=True, fastmath=True)
def endpoint_terms(d, z, zt, zg, nd, gsum, j, ztx, tx_agl, rx_h, lam, f_mhz, model,
                   clut_mode, excl_m, cl_R, cl_typ, fl_R, fl_typ, two_ray, veg_g, veg_am, bld, bld_mode):
    """Loss terms for receiver at index j.
    d: distances, z: obstacle heights (bulge-corrected), zt: scratch copy of z,
    zg: ground (bulge-corrected), nd: obstacle height above ground, gsum: cumulative
    raw ground sum (for effective tx height).
    bld: 1 where the obstacle at a sample is a known building; bld_mode 1 = buildings act only through
    diffraction (no P.833 attenuation through them), 0 = like vegetation.
    Returns (diffraction dB, v_main, clutter dB, ground-reflection dB, clutter height R,
             obstacle/vegetation dB, path length through clutter layer m)."""
    zrx = zg[j] + rx_h
    lo = j
    R_dsm = 0.0
    # exclusion zone at most half the path: otherwise obstacles between transmitter and receiver vanish from
    # the diffraction on short paths (#25)
    ex = min(excl_m, 0.5 * d[j])
    if clut_mode > 0 and excl_m > 0.0:
        while lo > 1 and d[j] - d[lo - 1] <= ex:
            lo -= 1
        for i in range(lo, j + 1):
            if nd[i] > R_dsm:
                R_dsm = nd[i]
        for i in range(lo, j):
            zt[i] = zg[i]
    ldiff, v1 = diffraction(d, zt, j, ztx, zrx, lam, model)
    for i in range(lo, j):
        zt[i] = z[i]
    f_ghz = f_mhz / 1000.0
    lclut = 0.0
    if clut_mode == 1:
        a1 = p2108_terminal(rx_h, R_dsm, f_ghz, 1) if R_dsm > rx_h + 0.5 else 0.0
        a2 = p2108_terminal(rx_h, fl_R, f_ghz, fl_typ)
        lclut = max(a1, a2)
        if a2 > a1:
            R_dsm = fl_R
    elif clut_mode == 2:
        lclut = p2108_terminal(rx_h, cl_R, f_ghz, cl_typ)
        R_dsm = cl_R
    lpe = 0.0
    if two_ray and j > 0 and d[j] > 1.0:
        mean_g = gsum[j] / j
        ht = max(tx_agl, ztx - mean_g)
        hr = max(rx_h, 0.5)
        l_pe = 40.0 * math.log10(d[j]) - 20.0 * math.log10(ht * hr)
        l_fs = 20.0 * math.log10(d[j] / 1000.0) + 20.0 * math.log10(f_mhz) + 32.44
        extra = l_pe - l_fs
        if extra > 0.0:
            # fade the reflection term out as the path becomes obstructed
            w = 1.0
            if v1 > -0.78:
                w = max(0.0, 1.0 - (v1 + 0.78) / 1.58)
            lpe = extra * w
    # ITU-R P.833: path length where the direct line runs *through* the clutter layer
    # (above ground, below the obstacle surface). Terrain blockage is left to diffraction;
    # the receiver's near zone is covered by the P.2108 term.
    lveg = 0.0
    dveg = 0.0
    if veg_g > 0.0 and j > 1:
        D = d[j]
        stop = lo if (clut_mode > 0 and excl_m > 0.0) else j
        for i in range(1, stop):
            if bld_mode == 1 and bld[i] == 1:
                continue
            li = ztx + (zrx - ztx) * d[i] / D
            if li < z[i] and li > zg[i]:
                dveg += d[i] - d[i - 1]
        if dveg > 0.0:
            lveg = veg_am * (1.0 - math.exp(-dveg * veg_g / veg_am))
    return ldiff, v1, lclut, lpe, R_dsm, lveg, dveg


@njit(parallel=True, cache=True, fastmath=True)
def coverage_rays(G, S, sx, sy, cs, kscale, ztx_abs, tx_agl, rx_h, lam, f_mhz, n_rays, n_bins, step, sub, re,
                  model, clut_mode, excl_m, cl_R, cl_typ, fl_R, fl_typ, two_ray, veg_g, veg_am, a0, a1,
                  G2, S2, sx2, sy2, cs2, rnear, B, B2, bld_mode):
    """Radial sweep. Returns per (ray, bin): diffraction, v_main, dz, clutter, reflection,
    obstacle/vegetation loss, and visibility of the surface (roof/tree top) from the antenna.
    G2/S2: fine grid (cell size cs2) around the station. Up to rnear (m) the profile is sampled internally in fine
    steps from this grid, so buildings right next to the antenna are not smeared by the coarse grid and enter the
    diffraction with front and back edge (#23). Output stays per coarse bin. rnear <= 0: off.
    B/B2: building masks (0/1) on the same two grids; bld_mode see endpoint_terms."""
    shape = (a1 - a0, n_bins + 1)  # only rays a0..a1-1 (progress in blocks)
    o_diff = np.zeros(shape, dtype=np.float32)
    o_v = np.full(shape, -1e9, dtype=np.float32)
    o_dz = np.zeros(shape, dtype=np.float32)
    o_cl = np.zeros(shape, dtype=np.float32)
    o_pe = np.zeros(shape, dtype=np.float32)
    o_veg = np.zeros(shape, dtype=np.float32)
    o_vis = np.zeros(shape, dtype=np.int8)
    # internal samples: fine up to the near-field limit (aligned to coarse bins), then the coarse bins
    fs = cs2 * kscale
    c0 = 0
    nf = 0
    if rnear > 0.0 and step > fs * 1.01:
        c0 = int(rnear / step)                     # coarse bins 1..c0 lie in the near field
        if c0 > n_bins:
            c0 = n_bins
        nf = int(((c0 + 0.5) * step) / fs)         # feine Punkte k*fs, k = 1..nf
    n_int = nf + (n_bins - c0)
    out_q = np.empty(n_bins + 1, dtype=np.int64)  # grober Bin -> interner Index
    out_q[0] = 0
    for jj in range(1, n_bins + 1):
        if jj <= c0:
            q = int(jj * step / fs + 0.5)
            if q < 1:
                q = 1
            if q > nf:
                q = nf
            out_q[jj] = q
        else:
            out_q[jj] = nf + (jj - c0)
    is_out = np.zeros(n_int + 1, dtype=np.int64) - 1
    for jj in range(1, n_bins + 1):
        is_out[out_q[jj]] = jj
    for r in prange(a1 - a0):
        a = a0 + r
        th = 2.0 * math.pi * a / n_rays
        ux = math.sin(th) / (kscale * cs)
        uy = -math.cos(th) / (kscale * cs)
        ux2 = math.sin(th) / (kscale * cs2)
        uy2 = -math.cos(th) / (kscale * cs2)
        d = np.empty(n_int + 1, dtype=np.float64)
        z = np.empty(n_int + 1, dtype=np.float64)
        zg = np.empty(n_int + 1, dtype=np.float64)
        nd = np.empty(n_int + 1, dtype=np.float64)
        gsum = np.empty(n_int + 1, dtype=np.float64)
        bld = np.zeros(n_int + 1, dtype=np.int8)
        d[0] = 0.0
        z[0] = ztx_abs
        zg[0] = 0.0
        nd[0] = 0.0
        gsum[0] = 0.0
        acc = 0.0
        for q in range(1, n_int + 1):
            mx = -1e9
            if q <= nf:  # feines Nahfeld
                dc = q * fs
                lo_d = dc - 0.5 * fs
                if q == 1:
                    lo_d = 0.6 * fs  # skip the antenna's own fine cell
                hi_d = dc + 0.5 * fs
                dmx = dc
                for k in range(3):
                    dd = lo_d + (k + 0.5) * (hi_d - lo_d) / 3.0
                    v = sample(S2, sx2 + ux2 * dd, sy2 + uy2 * dd)
                    if v > mx:
                        mx = v
                        dmx = dd
                g = sample(G2, sx2 + ux2 * dc, sy2 + uy2 * dc)
                bld[q] = mask_at(B2, sx2 + ux2 * dmx, sy2 + uy2 * dmx)
            else:
                i = c0 + (q - nf)
                dc = i * step
                lo_d = dc - 0.5 * step
                if nf > 0 and lo_d < nf * fs + 0.5 * fs:
                    lo_d = nf * fs + 0.5 * fs  # continue right after the near field
                hi_d = dc + 0.5 * step
                dmx = dc
                for k in range(sub):
                    dd = lo_d + (k + 0.5) * (hi_d - lo_d) / sub
                    v = sample(S, sx + ux * dd, sy + uy * dd)
                    if v > mx:
                        mx = v
                        dmx = dd
                g = sample(G, sx + ux * dc, sy + uy * dc)
                bld[q] = mask_at(B, sx + ux * dmx, sy + uy * dmx)
            if mx < g:
                mx = g
            bulge = dc * dc / (2.0 * re)
            d[q] = dc
            z[q] = mx - bulge
            zg[q] = g - bulge
            nd[q] = mx - g
            acc += g
            gsum[q] = acc
        zt = z.copy()
        max_slope = -1e18
        for q in range(1, n_int + 1):
            j = is_out[q]
            if j > 0:
                # optical visibility of the surface (+1 m) at bin j from the antenna
                if (z[q] + 1.0 - ztx_abs) / d[q] >= max_slope:
                    o_vis[r, j] = 1
                ldiff, v1, lcl, lpe, _r, lveg, _dv = endpoint_terms(
                    d, z, zt, zg, nd, gsum, q, ztx_abs, tx_agl, rx_h, lam, f_mhz,
                    model, clut_mode, excl_m, cl_R, cl_typ, fl_R, fl_typ, two_ray, veg_g, veg_am, bld, bld_mode)
                o_veg[r, j] = lveg
                o_diff[r, j] = ldiff
                o_v[r, j] = v1
                o_dz[r, j] = zg[q] + rx_h - ztx_abs
                o_cl[r, j] = lcl
                o_pe[r, j] = lpe
            sl = (z[q] - ztx_abs) / d[q]
            if sl > max_slope:
                max_slope = sl
    return o_diff, o_v, o_dz, o_cl, o_pe, o_veg, o_vis


# ITU-R P.833-8 measured values (St. Petersburg mixed forest): f MHz, gamma dB/m, Am dB
_P833 = [(105.9, 0.04, 9.4), (466.475, 0.12, 18.0), (949.0, 0.17, 26.5), (1852.2, 0.30, 29.0), (2117.5, 0.34, 34.1)]


def p833_params(f_mhz):
    """Specific attenuation and max. excess attenuation, log-frequency interpolation of P.833-8 table."""
    pts = _P833
    lf = math.log(max(f_mhz, 30.0))
    if lf <= math.log(pts[0][0]):
        a, b = pts[0], pts[1]
    elif lf >= math.log(pts[-1][0]):
        a, b = pts[-2], pts[-1]
    else:
        for a, b in zip(pts, pts[1:]):
            if math.log(a[0]) <= lf <= math.log(b[0]):
                break
    t = (lf - math.log(a[0])) / (math.log(b[0]) - math.log(a[0]))
    g = max(0.005, a[1] + t * (b[1] - a[1]))
    tc = min(1.0, max(0.0, t))  # do not extrapolate the maximum beyond the measured range
    am = max(3.0, a[2] + tc * (b[2] - a[2]))
    return g, am


def veg_params(s, f_mhz):
    mode = s.get("veg_mode", "auto")
    if mode == "off":
        return 0.0, 1.0
    g, am = p833_params(f_mhz)
    if mode == "custom":
        g = float(s.get("veg_gamma_db_m") or g)
        am = float(s.get("veg_max_db") or am)
    return g, am


def antenna_gain(az_deg, elev_deg, ant):
    """Vectorised antenna gain (dBi) towards azimuth/elevation (degrees)."""
    g = float(ant.get("gain_dbi", 0.0))
    fb = float(ant.get("fb_db", 25.0) or 25.0)
    tilt = float(ant.get("tilt_deg", 0.0) or 0.0)  # positive = down-tilt
    vbw = ant.get("vbw_deg")
    if not vbw:
        vbw = auto_vbw(ant)
    vbw = float(vbw)
    att_v = np.minimum(12.0 * ((elev_deg + tilt) / vbw) ** 2, fb)
    if ant.get("type", "omni") == "omni":
        att = att_v
    else:
        hbw = float(ant.get("hbw_deg", 60.0) or 60.0)
        daz = (az_deg - float(ant.get("azimuth_deg", 0.0) or 0.0) + 180.0) % 360.0 - 180.0
        att_h = np.minimum(12.0 * (daz / hbw) ** 2, fb)
        att = np.minimum(att_h + att_v, fb)
    att = att + sector_loss(az_deg, ant)
    return g - att


def sector_loss(az_deg, ant):
    """#23: open radiation sector (e.g. an antenna on a house wall). Outside the sector sector_loss_db is added,
    with a soft 15° transition at the edges – not a directional pattern; inside, the omni stays unchanged."""
    if not ant.get("sector_on"):
        return 0.0
    width = min(359.0, max(10.0, float(ant.get("sector_width_deg", 180.0) or 180.0)))
    loss = max(0.0, float(ant.get("sector_loss_db", 25.0) or 0.0))
    daz = np.abs((np.asarray(az_deg, dtype=np.float64) - float(ant.get("sector_az_deg", 0.0) or 0.0) + 180.0) % 360.0 - 180.0)
    edge = 15.0
    t = np.clip((daz - width / 2.0) / edge + 0.5, 0.0, 1.0)  # 0 inside, 1 outside
    return loss * t


def auto_vbw(ant):
    g = float(ant.get("gain_dbi", 2.15))
    if ant.get("type", "omni") == "omni":
        return max(3.0, min(120.0, 78.0 / 10 ** ((g - 2.15) / 10.0)))
    hbw = float(ant.get("hbw_deg", 60.0) or 60.0)
    return max(2.0, min(120.0, 32400.0 / (hbw * 10 ** (g / 10.0))))


def fspl_db(d_m, f_mhz):
    d_km = np.maximum(d_m, 1.0) / 1000.0
    return 20.0 * np.log10(d_km) + 20.0 * math.log10(f_mhz) + 32.44


def hata_db(d_m, f_mhz, hb, hm, env="urban"):
    """Okumura-Hata (small/medium city) – for comparison only.
    Valid roughly 150–1500 MHz, 1–20 km, hb 30–200 m, hm 1–10 m."""
    d_km = max(d_m / 1000.0, 0.05)
    hb = max(hb, 1.0)
    lf = math.log10(f_mhz)
    a_hm = (1.1 * lf - 0.7) * hm - (1.56 * lf - 0.8)
    L = 69.55 + 26.16 * lf - 13.82 * math.log10(hb) - a_hm + (44.9 - 6.55 * math.log10(hb)) * math.log10(d_km)
    if env == "suburban":
        L -= 2 * math.log10(f_mhz / 28.0) ** 2 + 5.4
    elif env == "open":
        L -= 4.78 * lf ** 2 - 18.33 * lf + 40.94
    return L


LORA_SNR = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}


def required_snr_db(st):
    """SNR the station's modulation needs (LoRa per spreading factor, otherwise the entered value)."""
    if st.get("mode") == "lora":
        return LORA_SNR.get(int(st.get("sf", 11)), -17.5)
    return float(st.get("req_snr_db", 10.0))


def noise_floor_dbm(st, noise_rise_db=0.0):
    """Noise at the station's receiver.

    A measured average noise floor entered for the station (#37, e.g. from the repeater's own statistics) wins –
    it already contains the local noise, so the global noise rise is not added. Otherwise thermal noise
    kTB = −174 dBm/Hz + 10·log10(B) plus the receiver noise figure and the global noise rise.
    """
    if st.get("noise_floor_dbm") not in (None, ""):
        return float(st["noise_floor_dbm"])
    bw_hz = float(st.get("bw_khz", 125.0)) * 1000.0
    return -174.0 + 10.0 * math.log10(bw_hz) + float(st.get("nf_db", 6.0)) + float(noise_rise_db)


def sensitivity_dbm(st, noise_rise_db=0.0):
    if st.get("sens_override_dbm") not in (None, ""):
        return float(st["sens_override_dbm"]) + float(noise_rise_db)
    return noise_floor_dbm(st, noise_rise_db) + required_snr_db(st)


def los_class(v):
    """2 = 60 % of first Fresnel zone clear, 1 = optical LOS, 0 = obstructed."""
    v = np.asarray(v)
    return np.where(v <= -0.6 * math.sqrt(2.0), 2, np.where(v <= 0.0, 1, 0)).astype(np.int8)


def clutter_params(s):
    """Map settings to kernel parameters."""
    mode = s.get("clutter_mode", "auto")
    fl_R, fl_typ = CLUTTER_CLASSES.get(s.get("clutter_floor", "none"), (0.0, 0))
    if mode == "off":
        return 0, 0.0, 0.0, 0, 0.0, 0
    excl = float(s.get("clutter_exclude_m", 60.0))
    if mode == "auto":
        return 1, excl, 0.0, 0, fl_R, fl_typ
    R, typ = CLUTTER_CLASSES.get(mode, (15.0, 1))
    return 2, excl, R, typ, 0.0, 0


def warmup():
    G = np.zeros((20, 20), dtype=np.float32)
    coverage_rays(G, G, 10.0, 10.0, 10.0, 0.6, 10.0, 10.0, 1.5, 0.35, 869.0, 8, 5, 10.0, 2, 8.5e6,
                  0, 1, 30.0, 0.0, 0, 10.0, 0, True, 0.17, 26.5, 0, 8, G, G, 10.0, 10.0, 5.0, 50.0,
                  np.zeros((2, 2), dtype=np.uint8), np.zeros((2, 2), dtype=np.uint8), 0)
