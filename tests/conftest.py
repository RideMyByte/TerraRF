"""Test setup: isolated data dir, no network, small synthetic terrain (EPSG:25832)."""
import os
import tempfile

import numpy as np
import pytest

_DATA = tempfile.mkdtemp(prefix="rfp-test-")
os.environ["RFP_DATA"] = _DATA
os.environ["RFP_PRELOAD"] = ""          # never download at startup in tests
os.environ["RFP_ADMIN_USER"] = "admin"
os.environ["RFP_ADMIN_PASSWORD"] = "test-pw"
os.environ["NUMBA_CACHE_DIR"] = os.path.join(_DATA, "numba")

import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

# station used in tests sits near 51.575 N / 7.02 E  ->  UTM32 ~ (362400, 5715700)
X0, Y1, RES, N = 355000.0, 5723000.0, 20.0, 750  # 15 km x 15 km


def _write(name, arr):
    d = os.path.join(_DATA, "dem", name)
    os.makedirs(d, exist_ok=True)
    prof = dict(driver="GTiff", width=N, height=N, count=1, dtype="float32", crs="EPSG:25832",
                transform=from_origin(X0, Y1, RES, RES), nodata=-9999)
    with rasterio.open(os.path.join(d, f"{name}.tif"), "w", **prof) as ds:
        ds.write(arr.astype(np.float32), 1)


@pytest.fixture(scope="session", autouse=True)
def terrain():
    yy, xx = np.mgrid[0:N, 0:N] * RES
    ground = 50 + 10 * np.sin(xx / 1500.0) + 40 * np.exp(-(((xx - 11000) ** 2 + (yy - 4000) ** 2) / 600.0 ** 2))
    surface = ground.copy()
    surface[300:340, 200:600] += 20  # a 0.8 km x 8 km "forest" band
    _write("tdgm", ground)
    _write("tdom", surface)
    return _DATA


@pytest.fixture
def station():
    return {"id": "a", "name": "A", "color": "#e6194b", "enabled": True, "lat": 51.575, "lon": 7.02,
            "height_m": 30, "height_ref": "ground", "tx_power_dbm": 22, "freq_mhz": 869.618, "bw_khz": 62.5,
            "mode": "lora", "sf": 8, "nf_db": 6, "cable_loss_db": 0.5, "radius_km": 4,
            "antenna": {"type": "omni", "gain_dbi": 5.8}}


@pytest.fixture
def project(station):
    return {"name": "Test", "settings": {"ground_source": "local:tdgm", "surface_source": "local:tdom", "res_m": 25},
            "stations": [station]}
