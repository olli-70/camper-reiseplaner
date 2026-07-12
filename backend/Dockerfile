FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAMPER_DB=/data/camper.db

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Backend-Code nach /app/app, PWA nach /app/frontend
COPY backend/app ./app
COPY frontend ./frontend

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
# S1: --proxy-headers wertet X-Forwarded-For des Reverse-Proxy (Caddy/Traefik) aus,
# damit request.client.host die ECHTE Client-IP ist (Login-Rate-Limiter wirksam).
# Port 8000 ist nicht ans Internet veröffentlicht – nur der Proxy im Docker-Netz
# erreicht den Container -> --forwarded-allow-ips=* (nur der Proxy ist der Peer).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
