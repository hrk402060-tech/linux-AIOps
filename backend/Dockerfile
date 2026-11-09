FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

RUN sed -i \
        -e 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
        -e 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install --yes --no-install-recommends fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system sentinel \
    && useradd --system --gid sentinel --home-dir /app sentinel

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt

COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY backend/src ./src
COPY backend/entrypoint.sh /usr/local/bin/sentinel-entrypoint
RUN chmod 0555 /usr/local/bin/sentinel-entrypoint \
    && chown -R sentinel:sentinel /app

USER sentinel
EXPOSE 8000
ENTRYPOINT ["sentinel-entrypoint"]
