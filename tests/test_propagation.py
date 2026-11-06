import math

import numpy as np

from app.model import propagation as prop


def test_knife_edge_reference_values():
    # ITU-R P.526: J(v) = 0 for v <= -0.78, ~6 dB at v = 0
    assert prop.knife_edge_loss(-1.0) == 0.0
    assert abs(prop.knife_edge_loss(0.0) - 6.0) < 0.1
    assert prop.knife_edge_loss(2.0) > prop.knife_edge_loss(1.0) > prop.knife_edge_loss(0.0)


def test_p2108_terminal_clutter():
    # 869 MHz, 1.5 m terminal: suburban (R 10, height gain) ~17.7 dB, urban (R 15) ~22.4 dB
    assert abs(prop.p2108_terminal(1.5, 10.0, 0.8696, 0) - 17.7) < 0.3
    assert abs(prop.p2108_terminal(1.5, 15.0, 0.8696, 1) - 22.4) < 0.3
    assert prop.p2108_terminal(20.0, 15.0, 0.8696, 1) == 0.0


def test_p833_params_interpolation():
    g, am = prop.p833_params(949.0)
    assert abs(g - 0.17) < 1e-6 and abs(am - 26.5) < 1e-6
    g, am = prop.p833_params(869.0)
    assert 0.15 < g < 0.17 and 24 < am < 26.5


def test_sensitivity_lora():
    st = {"mode": "lora", "sf": 12, "bw_khz": 125, "nf_db": 6}
    # SX126x datasheet SF12/125 kHz is about -137 dBm
    assert abs(prop.sensitivity_dbm(st) - (-137.0)) < 0.5
    assert abs(prop.sensitivity_dbm(st, 6) - (-131.0)) < 0.5


def test_obstructed_path_costs_more_than_free():
    n = 200
    d = np.linspace(0, 2000, n + 1)
    ground = np.full(n + 1, 50.0)
    surf = ground.copy()
    lam = 3e8 / 869e6
    gsum = np.concatenate([[0.0], np.cumsum(ground[1:])])

    def terms(surface):
        z = surface.copy()
        z[0] = 80.0
        nd = surface - ground
        nd[0] = 0
        return prop.endpoint_terms(d, z, z.copy(), ground.copy(), nd, gsum, n, 80.0, 30.0, 1.5, lam, 869.0, 1,
                                   0, 0.0, 0.0, 0, 0.0, 0, False, *prop.p833_params(869.0),
                                   np.zeros(n + 1, dtype=np.int8), 0)

    free = terms(surf)
    surf2 = surf.copy()
    surf2[80:140] += 25.0  # 600 m forest block in the path
    blocked = terms(surf2)
    assert blocked[0] > free[0] + 10       # diffraction
    assert blocked[5] > 10                 # obstacle (P.833) term
    assert math.isclose(free[5], 0.0)


def test_hata_reference():
    # Okumura-Hata urban, 869 MHz, hb 30 m, hm 1.5 m, 2.6 km  ~ 140 dB
    assert abs(prop.hata_db(2600, 869, 30, 1.5) - 140.5) < 1.5
