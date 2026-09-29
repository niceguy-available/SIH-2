# Full bookworm image: rasterio wheels need the system libexpat it ships.
FROM python:3.11-bookworm
WORKDIR /app
COPY requirements.runtime.txt .
RUN pip install --no-cache-dir -r requirements.runtime.txt
COPY lib ./lib
COPY models ./models
COPY routers ./routers
COPY server.py .
# Curated LROC catalogue; runtime data lives in the /data volume.
COPY data/footprints.json /app/catalog/footprints.json
COPY docker-entrypoint.sh .
ENV DATA_ROOT=/data
EXPOSE 8001
ENTRYPOINT ["sh", "docker-entrypoint.sh"]
# Hosts such as Render pass the port in $PORT.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001}"]
