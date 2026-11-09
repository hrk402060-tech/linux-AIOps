#!/usr/bin/env bash
set -euo pipefail

root=$(realpath "${1:?isolated candidate directory required}")
mode=${2:-tests}
case "$root" in
  /var/tmp/linux-ops-trust-*) ;;
  *) printf 'Refusing non-candidate workspace\n' >&2; exit 2 ;;
esac
test -f "$root/backend/alembic.ini"
test -f "$root/frontend/package.json"

run_logged() {
  local label=$1
  shift
  if "$@" >"$root/$label.log" 2>&1; then
    printf '%s: passed\n' "$label"
    tail -n 5 "$root/$label.log"
  else
    printf '%s: failed (details retained in isolated candidate)\n' "$label" >&2
    return 1
  fi
}

backend() {
  docker run --rm --network none --user 0 --entrypoint python \
    -v "$root/backend:/app:ro" \
    -v linux_ops_readonly_test_python:/test-deps:ro \
    --tmpfs /preview:rw,mode=1777 \
    -e PYTHONPATH=/app:/test-deps \
    -e CREDENTIAL_MASTER_KEY="$(openssl rand -base64 32)" \
    -e API_ADMIN_TOKEN="$(openssl rand -hex 32)" \
    -w /app linux-aiops-platform-backend "$@"
}

frontend() {
  docker run --rm --network none --entrypoint node \
    -v "$root/frontend:/app" \
    -v /opt/linux-ops-platform/frontend/node_modules:/app/node_modules:ro \
    --tmpfs /app/node_modules/.vite:rw,mode=1777 \
    --tmpfs /app/node_modules/.vite-temp:rw,mode=1777 \
    -w /app node:22-bookworm "$@"
}

case "$mode" in
  tests)
    run_logged backend-tests backend -m pytest -q --tb=short \
      tests/test_dashboard_api.py tests/test_system_api.py tests/test_probes_api.py \
      tests/test_patches_api.py tests/test_version_safety.py
    run_logged backend-lint backend -m ruff check --no-cache \
      src/services/dashboard.py src/schemas/dashboard.py src/routes/system.py \
      src/routes/patches.py src/services/version_safety.py src/routes/probes.py \
      src/services/probe_policy.py src/schemas/probe_policy.py src/models/platform_setting.py \
      tests/test_dashboard_api.py tests/test_system_api.py tests/test_probes_api.py \
      tests/test_patches_api.py tests/test_version_safety.py
    run_logged frontend-tests frontend node_modules/vitest/vitest.mjs run \
      src/components/GlobalSearch.test.tsx src/components/AppShell.test.tsx \
      src/App.navigation.test.tsx src/pages/AssetsPage.test.tsx \
      src/pages/CmdbPage.test.tsx src/pages/VulnerabilitiesPage.test.tsx \
      src/pages/DashboardPage.test.tsx src/pages/ObservabilityPage.test.tsx \
      src/pages/ReportsPage.test.tsx src/pages/BaselinesPage.test.tsx \
      src/pages/ProbesPage.test.tsx src/pages/SystemPage.test.tsx \
      src/pages/PatchesPage.test.tsx src/pages/DataTrust.test.tsx
    ;;
  build)
    run_logged frontend-types frontend node_modules/typescript/bin/tsc -b --pretty false
    run_logged frontend-build frontend node_modules/vite/bin/vite.js build
    ;;
  focused)
    run_logged frontend-search frontend node_modules/vitest/vitest.mjs run \
      src/components/GlobalSearch.test.tsx
    run_logged frontend-focused frontend node_modules/vitest/vitest.mjs run \
      src/pages/DataTrust.test.tsx \
      src/pages/CmdbPage.test.tsx src/pages/ProbesPage.test.tsx \
      src/pages/AssetsPage.test.tsx \
      -t 'search|Search|first-load data truthfulness|human action|short cleanup reasons|late preview'
    ;;
  migration)
    network="trust-migration-$(basename "$root")"
    postgres="trust-postgres-$(basename "$root")"
    cleanup() {
      docker rm -f "$postgres" >/dev/null 2>&1 || true
      docker network rm "$network" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT
    password=$(openssl rand -hex 24)
    docker network create --internal "$network" >/dev/null
    docker run -d --name "$postgres" --network "$network" --network-alias trust-postgres \
      -e POSTGRES_USER=trust -e POSTGRES_DB=trust -e POSTGRES_PASSWORD="$password" \
      --tmpfs /var/lib/postgresql/data:rw,mode=1777 postgres:16-alpine >/dev/null
    for _ in $(seq 1 30); do
      if docker exec "$postgres" pg_isready -U trust -d trust >/dev/null 2>&1; then break; fi
      sleep 1
    done
    run_logged postgres-migration docker run --rm --network "$network" --user 0 --entrypoint python \
      -v "$root/backend:/app:ro" -v "$root/scripts:/verification:ro" -w /app \
      -e PYTHONPATH=/app -e TRUST_MIGRATION_ISOLATED=1 \
      -e CREDENTIAL_MASTER_KEY="$(openssl rand -base64 32)" \
      -e API_ADMIN_TOKEN="$(openssl rand -hex 32)" \
      -e DATABASE_URL="postgresql+psycopg://trust:$password@trust-postgres/trust" \
      linux-aiops-platform-backend /verification/verify_data_trust_migration.py
    ;;
  *)
    printf 'Unsupported verification mode\n' >&2
    exit 2
    ;;
esac
