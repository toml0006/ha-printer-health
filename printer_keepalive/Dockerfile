FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends cups-ipp-utils fonts-dejavu-core tzdata \
    && python -m pip install --no-cache-dir pillow paho-mqtt qrcode zeroconf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/app.py
COPY designs/ /app/designs/

EXPOSE 8099

CMD ["python", "/app/app.py"]
