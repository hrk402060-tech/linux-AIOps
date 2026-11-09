#!/bin/bash
set -euo pipefail

root=$(realpath "${1:?Pass the isolated candidate directory}")
case "$root" in
  /var/tmp/linux-ops-mysql-*) ;;
  *) printf 'Refusing non-isolated directory\n' >&2; exit 2 ;;
esac
if [ -e "$root/deploy/.env" ] || [ -e "$root/backend/.env" ]; then
  printf 'Candidate must not contain deployment credentials\n' >&2
  exit 2
fi
name="mysql-check-$(date +%s)-$$"
cleanup() {
  docker rm -f "$name-backend" "$name-frontend" >/dev/null 2>&1 || true
}
trap cleanup EXIT
mkdir -p "$root/results"

if [ "${MYSQL_SKIP_BACKEND:-0}" != 1 ]; then
if ! timeout 150s docker run --rm --name "$name-backend" --network none --read-only \
  --tmpfs /tmp:rw,exec \
  --mount "type=bind,src=$root/backend,dst=/app,readonly" \
  --mount type=volume,src=linux_ops_readonly_test_python,dst=/deps,readonly \
  --workdir /app -e PYTHONPATH=/deps:/app -e PYTHONDONTWRITEBYTECODE=1 \
  -e "MYSQL_SKIP_TESTS=${MYSQL_SKIP_TESTS:-0}" \
  -e "MYSQL_TEST_TARGETS=${MYSQL_TEST_TARGETS:-tests/test_mysql_grants.py tests/test_mysql_diagnostics.py tests/test_mysql_transport.py tests/test_mysql_tls.py tests/test_diagnostic_grants.py tests/test_readonly_diagnostics_policy.py}" \
  --entrypoint sh linux-vuln-sentinel-backend -c '
    if [ "$MYSQL_SKIP_TESTS" != 1 ]; then
      python -m pytest -q -p no:cacheprovider $MYSQL_TEST_TARGETS || exit $?
    fi
    python -m ruff check --no-cache src/services/mysql_grants.py src/services/mysql_diagnostics.py \
      src/services/mysql_transport.py src/services/readonly_diagnostics.py src/schemas/mysql_grant.py \
      src/services/diagnostic_grants.py src/routes/diagnostic_grants.py tests/test_mysql*.py
  ' >"$root/results/backend.log" 2>&1; then
  tail -n 45 "$root/results/backend.log"
  exit 1
fi
tail -n 4 "$root/results/backend.log"
fi

if ! timeout 180s docker run --rm --name "$name-frontend" --network none \
  --mount "type=bind,src=$root/frontend,dst=/app" \
  --mount type=volume,src=linux_ops_frontend_node_modules_tmp,dst=/app/node_modules \
  -e "MYSQL_FRONTEND_PATTERN=${MYSQL_FRONTEND_PATTERN:-.}" \
  --workdir /app --entrypoint sh node:22-alpine -c '
    node_modules/.bin/vitest run src/components/events/MySQLGrantForm.test.tsx \
      src/components/events/DiagnosticGrantsTab.mysql.test.tsx src/components/events/AlertWorkflow.test.tsx \
      --reporter=dot --testNamePattern "$MYSQL_FRONTEND_PATTERN" &&
    node_modules/.bin/tsc -b && node_modules/.bin/vite build
  ' >"$root/results/frontend.log" 2>&1; then
  tail -n 45 "$root/results/frontend.log"
  exit 1
fi
tail -n 16 "$root/results/frontend.log"
