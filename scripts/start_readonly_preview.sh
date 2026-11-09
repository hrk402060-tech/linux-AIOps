#!/bin/bash
# Launch only the isolated, explicitly labelled workflow preview.
set -euo pipefail

workspace="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$workspace" != /tmp/linux-ops-readonly-preview-* ]]; then
    printf 'Preview must run from its isolated /tmp workspace.\n' >&2
    exit 1
fi
for name in linux-ops-readonly-preview-api linux-ops-readonly-preview-web; do
    if docker container inspect "$name" >/dev/null 2>&1; then
        printf 'Preview container already exists: %s\n' "$name" >&2
        exit 1
    fi
done
if [[ ! -f "$workspace/frontend/dist/index.html" ]]; then
    printf 'Build the isolated frontend before starting preview.\n' >&2
    exit 1
fi
network=linux-ops-readonly-preview
edge_network=linux-ops-readonly-preview-web-edge
if ! docker network inspect "$edge_network" >/dev/null 2>&1; then
    docker network create "$edge_network" >/dev/null
fi
if ! docker network inspect "$network" >/dev/null 2>&1; then
    docker network create --internal "$network" >/dev/null
fi
if [[ "$(docker network inspect "$network" --format '{{.Internal}}')" != true ]]; then
    printf 'Preview network must deny external egress.\n' >&2
    exit 1
fi
key="$(openssl rand -base64 32)"
token="$(openssl rand -hex 32)"
password="$(openssl rand -hex 10)!Aa1"
volume=linux_ops_readonly_preview_data
if docker volume inspect "$volume" >/dev/null 2>&1; then
    printf 'Existing preview data volume will not be overwritten.\n' >&2
    exit 1
fi
docker volume create "$volume" >/dev/null
common=(--user 0 --read-only --cap-drop=ALL --security-opt=no-new-privileges
    --label com.docker.compose.project=readonly-preview --label com.docker.compose.service=preview-api
    --network "$network" --tmpfs /tmp:rw,noexec,nosuid,size=64m
    -v "$workspace/backend/src:/app/src:ro" -v "$workspace/scripts:/scripts:ro"
    -v "$volume:/preview" -e DATABASE_URL=sqlite:////preview/workflow.db
    -e AUTO_CREATE_SCHEMA=true -e "CREDENTIAL_MASTER_KEY=$key"
    -e "API_ADMIN_TOKEN=$token" -e AUTH_COOKIE_NAME=sentinel_preview_session
    -e ALLOWED_ORIGINS=http://192.168.10.17:8089 -e PYTHONDONTWRITEBYTECODE=1)
docker run --rm "${common[@]}" -v linux_ops_readonly_test_python:/testdeps:ro \
    -e PYTHONPATH=/testdeps:/app -e "PREVIEW_PASSWORD=$password" \
    --entrypoint python linux-aiops-platform-backend /scripts/preview_readonly_workflow.py

docker run -d --name linux-ops-readonly-preview-api "${common[@]}" \
    --network-alias backend --entrypoint python linux-aiops-platform-backend \
    -m uvicorn src.main:app --host 0.0.0.0 --port 8000 >/dev/null
for attempt in {1..20}; do
    if docker exec linux-ops-readonly-preview-api python -c \
        'from urllib.request import urlopen; assert urlopen("http://127.0.0.1:8000/api/health", timeout=2).status == 200' \
        >/dev/null 2>&1; then
        break
    fi
    if [[ "$attempt" == 20 ]]; then
        printf 'Isolated preview backend did not become healthy.\n' >&2
        exit 1
    fi
    sleep 1
done
docker create --name linux-ops-readonly-preview-web --network "$edge_network" \
    --label com.docker.compose.project=readonly-preview --label com.docker.compose.service=preview-web \
    --publish 192.168.10.17:8089:80 \
    -v "$workspace/frontend/dist:/usr/share/nginx/html:ro" \
    -v "$workspace/frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
    linux-aiops-platform-frontend >/dev/null
docker network connect "$network" linux-ops-readonly-preview-web
docker start linux-ops-readonly-preview-web >/dev/null
printf 'Preview: http://192.168.10.17:8089/\nUser: preview-admin\nPassword: %s\n' "$password"
