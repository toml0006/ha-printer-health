FROM node:20-alpine AS ui-build
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ .
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends cups-ipp-utils fonts-dejavu-core tzdata \
    && python -m pip install --no-cache-dir pillow paho-mqtt qrcode zeroconf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ARG BUILD_VERSION=unknown
LABEL io.hass.version="${BUILD_VERSION}"
COPY app.py /app/app.py
COPY --from=ui-build /ui/dist /app/ui-dist
COPY fonts/ /app/fonts/

EXPOSE 8099

CMD ["python", "/app/app.py"]
