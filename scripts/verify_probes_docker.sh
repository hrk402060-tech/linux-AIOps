#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
network="sentinel-probe-check-$$"
fixture="sentinel-probe-fixture-$$"
backend="sentinel-probe-backend-$$"
cleanup() {
    docker rm -f "$backend" "$fixture" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create "$network" >/dev/null
docker run -d --name "$fixture" --network "$network" --entrypoint python \
    linux-vuln-sentinel-backend -m http.server 8080 >/dev/null
ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$fixture")
export API_ADMIN_TOKEN
API_ADMIN_TOKEN=$(openssl rand -hex 32)
master_key=$(openssl rand -base64 32)
docker run -d --name "$backend" --network "$network" \
    -e DATABASE_URL=sqlite:////tmp/probe-verification.db \
    -e API_ADMIN_TOKEN -e CREDENTIAL_MASTER_KEY="$master_key" \
    -e SENTINEL_PROBE_ALLOWED_CIDRS="$ip/32" \
    linux-vuln-sentinel-backend >/dev/null
for _ in $(seq 1 30); do
    if docker exec "$backend" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker cp scripts/verify_probes.py "$backend":/tmp/verify_probes.py
docker exec -e PROBE_FIXTURE_URL="http://$ip:8080/" "$backend" python /tmp/verify_probes.py
