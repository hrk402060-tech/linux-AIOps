FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY alert_relay.py /app/alert_relay.py
RUN useradd --system --no-create-home --shell /usr/sbin/nologin alertrelay
USER alertrelay
EXPOSE 8080
ENTRYPOINT ["python", "/app/alert_relay.py"]
