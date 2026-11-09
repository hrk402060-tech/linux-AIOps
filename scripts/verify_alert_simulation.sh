#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$root" in
    /opt/linux-ops-platform|/opt/linux-ops-platform/*)
        printf 'Refusing to run from the production tree\n' >&2
        exit 1
        ;;
esac

case "${1:-target}" in
    target)
        command=(python -m pytest -q -p no:cacheprovider --tb=short --disable-warnings
            tests/test_alert_recovery_simulation.py)
        ;;
    regression)
        command=(python -m pytest -q -p no:cacheprovider --tb=short --disable-warnings
            tests/test_alert_simulation.py tests/test_alert_loop.py
            tests/test_alert_payloads.py tests/test_alert_preflight.py
            tests/test_recovery_api.py tests/test_recovery_evidence.py
            tests/test_recovery_boundaries.py tests/test_recovery_current_integration.py)
        ;;
    simulation)
        command=(python /workspace/scripts/run_alert_simulation.py)
        ;;
    lint)
        command=(python -m ruff check /workspace/scripts/run_alert_simulation.py
            tests/test_alert_recovery_simulation.py)
        ;;
    *)
        printf 'Usage: %s {target|regression|simulation|lint}\n' "$0" >&2
        exit 2
        ;;
esac

# Generate disposable secrets; never inherit production configuration.
key="$(openssl rand -base64 32)"
token="$(openssl rand -hex 32)"
docker run --rm --pull never --network none --read-only \
    --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /preview:rw,mode=1777,size=256m --tmpfs /tmp:rw,mode=1777,size=256m \
    -e DATABASE_URL=sqlite:////preview/alert-simulation.db -e ALERT_SIMULATION_ONLY=1 \
    -e CREDENTIAL_MASTER_KEY="$key" -e API_ADMIN_TOKEN="$token" -e AUTH_BOOTSTRAP_PASSWORD= \
    -e AUTO_CREATE_SCHEMA=true -e PYTHONDONTWRITEBYTECODE=1 -e RUFF_NO_CACHE=true \
    -e PYTHONPATH=/test-deps:/workspace/backend \
    -v "${ALERT_SIMULATION_TEST_DEPS:-linux_ops_readonly_test_python}:/test-deps:ro" \
    -v "$root:/workspace:ro" -w /workspace/backend \
    --entrypoint "${command[0]}" \
    "${ALERT_SIMULATION_IMAGE:-linux-vuln-sentinel-backend:latest}" "${command[@]:1}"
