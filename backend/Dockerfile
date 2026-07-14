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
#
# NICHT "*": bei "*" nimmt uvicorn den LINKESTEN XFF-Eintrag – der ist client-
# gesetzt und damit spoofbar (Angreifer faket pro Request eine IP -> frischer
# Rate-Limit-Bucket, Brute-Force-Schutz umgangen). Stattdessen wird nur dem
# Docker-internen Proxy vertraut: das Image ist fuer dev (Traefik) UND prod
# (Caddy) dasselbe, und die konkreten Bridge-Subnetze werden je Host dynamisch
# vergeben – aber ALLE liegen im privaten Docker-Bereich 172.16.0.0/12. Port 8000
# ist nicht ans Internet veroeffentlicht. -> uvicorn nimmt den ersten Hop
# AUSSERHALB 172.16.0.0/12 = die echte, vom Proxy angehaengte Client-IP.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "172.16.0.0/12"]
