"""Synthetic test terrain (EPSG:25832) around Gladbeck: rolling hills, a 'Halde', and building blocks."""
import sys, os, numpy as np, rasterio
from rasterio.transform import from_origin
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dem"
res = 5.0
x0, y1 = 352000.0, 5728000.0   # approx. Gladbeck/Gelsenkirchen area UTM32
nx = ny = 6000                  # 30 km x 30 km
xx, yy = np.meshgrid(np.arange(nx) * res, np.arange(ny) * res)
g = 40 + 8 * np.sin(xx / 2500) * np.cos(yy / 3100) + 0.0004 * yy
for cx, cy, h, r in [(15000, 9000, 70, 900), (6000, 20000, 45, 700), (24000, 18000, 55, 1200)]:
    d = np.hypot(xx - cx, yy - cy)
    g += h * np.clip(1 - (d / r) ** 2, 0, None)
g = g.astype(np.float32)
s = g.copy()
rng = np.random.default_rng(1)
for _ in range(2500):
    bx, by = rng.integers(0, nx - 10), rng.integers(0, ny - 10)
    s[by:by + rng.integers(2, 8), bx:bx + rng.integers(2, 8)] += rng.uniform(6, 25)
for name, arr in (("dgm", g), ("dom", s)):
    os.makedirs(os.path.join(out, name), exist_ok=True)
    prof = dict(driver="GTiff", width=nx, height=ny, count=1, dtype="float32", crs="EPSG:25832",
                transform=from_origin(x0, y1, res, res), tiled=True, compress="deflate", nodata=-9999)
    with rasterio.open(os.path.join(out, name, f"test_{name}.tif"), "w", **prof) as ds:
        ds.write(arr, 1)
print("ok", out)
