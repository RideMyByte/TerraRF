"""Where TerraRF keeps its files.

Everything persistent lives below one directory (``RFP_DATA``, default ``/data`` – the Docker volume):
projects, measurements, catalogs, settings, downloaded elevation tiles and computation caches.
"""
import os

DATA_DIR = os.environ.get("RFP_DATA", "/data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")   # safe to delete; rebuilt on demand
DEM_DIR = os.path.join(DATA_DIR, "dem")       # elevation tiles and local GeoTIFFs

for _d in (DATA_DIR, CACHE_DIR, DEM_DIR):
    os.makedirs(_d, exist_ok=True)
