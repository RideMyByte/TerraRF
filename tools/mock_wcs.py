"""Minimal WCS 2.0.1 GetCoverage mock (for tests). MOCK_SCALING=0 rejects SCALESIZE/SCALEFACTOR."""
import io, os, re, numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.transform import from_origin
from fastapi import FastAPI, Request
from fastapi.responses import Response
SRC = os.environ.get("MOCK_SRC", "/tmp/rfdata/dem/dgm/test_dgm.tif")
SCALING = os.environ.get("MOCK_SCALING", "1") == "1"
app = FastAPI()
hits = {"n": 0}
CAPS = """<?xml version="1.0"?><wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0"><wcs:Contents>
<wcs:CoverageSummary><wcs:CoverageId>{cid}</wcs:CoverageId></wcs:CoverageSummary></wcs:Contents></wcs:Capabilities>"""
@app.get("/wcs_dom")
def wcs_dom(request: Request):
    return wcs(request, os.environ.get("MOCK_SRC_DOM", "/tmp/rfdata/dem/dom/test_dom.tif"), "nw_dom_test")
@app.get("/wcs")
def wcs(request: Request, src=None, cid="nw_dgm"):
    q = request.query_params
    hits["n"] += 1
    if q.get("REQUEST") == "GetCapabilities":
        return Response(CAPS.format(cid=cid).encode(), media_type="text/xml")
    if q.get("COVERAGEID") != cid:
        return Response(b"<ows:ExceptionReport>unknown coverage</ows:ExceptionReport>", status_code=404, media_type="text/xml")
    SRC_ = src or SRC
    subs = q.getlist("SUBSET")
    x0, x1 = map(float, re.match(r"x\((.*),(.*)\)", subs[0]).groups())
    y0, y1 = map(float, re.match(r"y\((.*),(.*)\)", subs[1]).groups())
    if ("SCALESIZE" in q or "SCALEFACTOR" in q) and not SCALING:
        return Response(b"<ows:ExceptionReport>scaling not supported</ows:ExceptionReport>", status_code=400, media_type="text/xml")
    with rasterio.open(SRC_) as ds:
        win = from_bounds(x0, y0, x1, y1, ds.transform)
        res = ds.res[0]
        w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
        m = re.match(r"x\((\d+)\),y\((\d+)\)", q.get("SCALESIZE", "")) if "SCALESIZE" in q else None
        if m: w, h = int(m.group(1)), int(m.group(2))
        elif "SCALESIZE" in q:
            return Response(b"<ows:ExceptionReport>bad axis</ows:ExceptionReport>", status_code=400, media_type="text/xml")
        data = ds.read(1, window=win, out_shape=(h, w), boundless=True, fill_value=-9999).astype(np.float32)
    buf = io.BytesIO()
    with rasterio.open(buf, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32", crs="EPSG:25832",
                       transform=from_origin(x0, y1, (x1 - x0) / w, (y1 - y0) / h), nodata=-9999) as out:
        out.write(data, 1)
    return Response(buf.getvalue(), media_type="image/tiff")
@app.get("/hits")
def h(): return hits
