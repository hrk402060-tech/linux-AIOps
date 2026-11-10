#!/usr/bin/env bash
set -euo pipefail
umask 077

# Run only against an isolated checkout on the authorized development server.
candidate=$(realpath "${1:?Pass the isolated candidate directory}")
case "$candidate" in /var/tmp/linux-ops-monitoring-*) ;; *) echo "Unexpected preview directory" >&2; exit 2 ;; esac
test -f "$candidate/backend/src/main.py"
test -d "$candidate/frontend/dist"
port=${2:-8097}
network=linux-ops-monitoring-preview
db=linux-ops-monitoring-preview-db
api=linux-ops-monitoring-preview-api
web=linux-ops-monitoring-preview-web
for name in "$db" "$api" "$web"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
        echo "Existing preview must be inspected before replacement: $name" >&2
        exit 2
    fi
done
if ss -lntH | awk '{print $4}' | grep -Eq ":${port}$"; then
    echo "Preview port is already in use" >&2
    exit 2
fi
docker network inspect "$network" >/dev/null 2>&1 || docker network create "$network" >/dev/null
preview_password=$(openssl rand -hex 24)
printf 'POSTGRES_USER=monitor\nPOSTGRES_DB=monitoring_preview\nPOSTGRES_PASSWORD=%s\n' "$preview_password" > "$candidate/database.env"
docker inspect linux-vuln-sentinel-backend-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | awk '!/^(DATABASE_URL|AUTH_COOKIE_NAME|AUTH_BOOTSTRAP_USERNAME|AUTH_BOOTSTRAP_PASSWORD|ALLOWED_ORIGINS|AUTO_CREATE_SCHEMA|DIAGNOSTIC_WORKERS_ENABLED)=/' > "$candidate/backend.env"
{
    printf 'DATABASE_URL=postgresql+psycopg://monitor:%s@%s:5432/monitoring_preview\n' "$preview_password" "$db"
    printf 'AUTH_COOKIE_NAME=linux_ops_monitoring_preview_session\nAUTH_BOOTSTRAP_USERNAME=\nAUTH_BOOTSTRAP_PASSWORD=\n'
    printf 'ALLOWED_ORIGINS=auto\nAUTO_CREATE_SCHEMA=false\nDIAGNOSTIC_WORKERS_ENABLED=false\n'
} >> "$candidate/backend.env"
docker run -d --name "$db" --network "$network" --env-file "$candidate/database.env" postgres:16-alpine >/dev/null
for attempt in $(seq 1 20); do
    if docker exec "$db" pg_isready -U monitor -d monitoring_preview >/dev/null 2>&1; then break; fi
    sleep 1
done
docker exec "$db" pg_isready -U monitor -d monitoring_preview >/dev/null
# Stream real records inside the server; no backup or plaintext credentials are exported.
docker exec linux-vuln-sentinel-postgres-1 sh -c 'pg_dump --no-owner --no-acl -U "$POSTGRES_USER" "$POSTGRES_DB"' \
    | docker exec -i "$db" psql -v ON_ERROR_STOP=1 -q -U monitor -d monitoring_preview >/dev/null
image=$(docker inspect linux-vuln-sentinel-backend-1 --format '{{.Image}}')
mounts=(-v "$candidate/backend/src:/app/src:ro" -v "$candidate/backend/migrations:/app/migrations:ro"
    -v "$candidate/backend/alembic.ini:/app/alembic.ini:ro")
docker run --rm --network "$network" --env-file "$candidate/backend.env" "${mounts[@]}" \
    --entrypoint alembic "$image" upgrade head > "$candidate/migration.log" 2>&1
# Lifespan is deliberately disabled: no scan, notification or collection schedulers.
docker run -d --name "$api" --network "$network" --env-file "$candidate/backend.env" "${mounts[@]}" \
    --entrypoint uvicorn "$image" src.main:app --host 0.0.0.0 --port 8000 --lifespan off >/dev/null
docker run -d --name "$web" --network "$network" -p "0.0.0.0:${port}:80" \
    -v "$candidate/frontend/dist:/usr/share/nginx/html:ro" \
    -v "$candidate/scripts/monitoring-preview.nginx.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine >/dev/null
printf 'Preview started on port %s; production containers and configuration unchanged.\n' "$port"
