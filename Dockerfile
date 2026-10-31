FROM python:3.12-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="TerraRF" \
      org.opencontainers.image.description="Funk-Abdeckung und Sichtlinien planen (MeshCore, Meshtastic, HAMNET) mit NRW-Höhendaten" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RFP_DATA=/data NUMBA_CACHE_DIR=/data/cache/numba APP_VERSION=${VERSION}

# rasterio-Wheels (manylinux) brauchen libexpat.so.1, die in python:*-slim fehlt.
# util-linux liefert setpriv für PUID/PGID.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 util-linux \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import rasterio, numba, fastapi; print('deps ok', rasterio.__version__)"

COPY app ./app
COPY tools ./tools
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
