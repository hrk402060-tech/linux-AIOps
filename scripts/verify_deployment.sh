#!/bin/bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"

set -a
# shellcheck disable=SC1091
source <(sed 's/\r$//' deploy/.env)
set +a

base_url="http://127.0.0.1:${APP_PORT:-8080}"
credential_name="docker-integration-$(date +%s)"
test_secret="deployment-secret-$(openssl rand -hex 12)"
response_file=$(mktemp)
asset_response_file=$(mktemp)
knowledge_response_file=$(mktemp)
coverage_response_file=$(mktemp)
backfill_response_file=$(mktemp)
scan_response_file=$(mktemp)
vulnerability_response_file=$(mktemp)
dashboard_response_file=$(mktemp)
agent_response_file=$(mktemp)
report_response_file=$(mktemp)
monitoring_response_file=$(mktemp)
events_response_file=$(mktemp)
audit_log_response_file=$(mktemp)
patches_response_file=$(mktemp)
agent_provider_response_file=$(mktemp)
provider_audit_response_file=$(mktemp)
system_response_file=$(mktemp)
auth_users_file=$(mktemp)
auth_user_file=$(mktemp)
auth_login_file=$(mktemp)
auth_me_file=$(mktemp)
auth_cookie_file=$(mktemp)
schedule_response_file=$(mktemp)
schedule_toggle_file=$(mktemp)
baseline_response_file=$(mktemp)
baseline_cancel_file=$(mktemp)
exposure_response_file=$(mktemp)
exposure_cancel_file=$(mktemp)
credential_risk_response_file=$(mktemp)
credential_risk_cancel_file=$(mktemp)
terminal_ticket_file=$(mktemp)
terminal_sessions_file=$(mktemp)
terminal_assistant_file=$(mktemp)
lifecycle_file=$(mktemp)
archive_file=$(mktemp)
cmdb_business_file=$(mktemp)
cmdb_application_file=$(mktemp)
cmdb_service_file=$(mktemp)
cmdb_api_file=$(mktemp)
cmdb_relation_file=$(mktemp)
cmdb_tree_file=$(mktemp)
cmdb_audit_file=$(mktemp)
incident_file=$(mktemp)
incident_timeline_file=$(mktemp)
trap 'rm -f "$response_file" "$asset_response_file" "$knowledge_response_file" "$coverage_response_file" "$backfill_response_file" "$scan_response_file" "$vulnerability_response_file" "$dashboard_response_file" "$agent_response_file" "$report_response_file" "$monitoring_response_file" "$events_response_file" "$audit_log_response_file" "$patches_response_file" "$agent_provider_response_file" "$provider_audit_response_file" "$system_response_file" "$auth_users_file" "$auth_user_file" "$auth_login_file" "$auth_me_file" "$auth_cookie_file" "$schedule_response_file" "$schedule_toggle_file" "$baseline_response_file" "$baseline_cancel_file" "$exposure_response_file" "$exposure_cancel_file" "$credential_risk_response_file" "$credential_risk_cancel_file" "$terminal_ticket_file" "$terminal_sessions_file" "$terminal_assistant_file" "$lifecycle_file" "$archive_file" "$cmdb_business_file" "$cmdb_application_file" "$cmdb_service_file" "$cmdb_api_file" "$cmdb_relation_file" "$cmdb_tree_file" "$cmdb_audit_file" "$incident_file" "$incident_timeline_file"' EXIT

for _ in $(seq 1 30); do
    health=$(docker inspect --format '{{.State.Health.Status}}' \
        linux-vuln-sentinel-frontend-1 2>/dev/null || true)
    if [[ "$health" == "healthy" ]]; then
        break
    fi
    sleep 1
done

docker compose --env-file deploy/.env ps

page_status=$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time 5 "$base_url/")
health_status=$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time 5 "$base_url/api/health")
unauthorized_status=$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time 5 "$base_url/api/v1/credentials")

auth_password="Verifier-$(openssl rand -hex 16)"
curl --silent --show-error --output "$auth_users_file" --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/auth/users"
auth_user_id=$(.venv-wsl/bin/python -c \
    'import json,sys; print(next((x["id"] for x in json.load(open(sys.argv[1])) if x["username"] == "deployment-auth-verifier"), ""))' \
    "$auth_users_file")
if [[ -z "$auth_user_id" ]]; then
    auth_create_payload=$(printf \
        '{"username":"deployment-auth-verifier","display_name":"部署认证验证","password":"%s","role":"operator"}' \
        "$auth_password")
    auth_create_status=$(curl --silent --show-error --output "$auth_user_file" \
        --write-out '%{http_code}' --max-time 5 \
        --header "Authorization: Bearer $API_ADMIN_TOKEN" \
        --header 'Content-Type: application/json' \
        --data-binary "$auth_create_payload" "$base_url/api/v1/auth/users")
    auth_user_id=$(.venv-wsl/bin/python -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$auth_user_file")
else
    auth_create_status=200
    curl --silent --show-error --output /dev/null --max-time 5 \
        --header "Authorization: Bearer $API_ADMIN_TOKEN" \
        --header 'Content-Type: application/json' \
        --data-binary "{\"password\":\"$auth_password\",\"must_change_password\":false}" \
        "$base_url/api/v1/auth/users/$auth_user_id/reset-password"
    curl --silent --show-error --output /dev/null --max-time 5 \
        --header "Authorization: Bearer $API_ADMIN_TOKEN" \
        --header 'Content-Type: application/json' --request PATCH \
        --data-binary '{"enabled":true}' "$base_url/api/v1/auth/users/$auth_user_id"
fi
auth_login_status=$(curl --silent --show-error --output "$auth_login_file" \
    --write-out '%{http_code}' --max-time 5 --cookie-jar "$auth_cookie_file" \
    --header 'Content-Type: application/json' \
    --data-binary "{\"username\":\"deployment-auth-verifier\",\"password\":\"$auth_password\"}" \
    "$base_url/api/v1/auth/login")
auth_me_status=$(curl --silent --show-error --output "$auth_me_file" \
    --write-out '%{http_code}' --max-time 5 --cookie "$auth_cookie_file" \
    "$base_url/api/v1/auth/me")
auth_logout_status=$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time 5 --cookie "$auth_cookie_file" \
    --request POST "$base_url/api/v1/auth/logout")
curl --silent --show-error --output /dev/null --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' --request PATCH \
    --data-binary '{"enabled":false}' "$base_url/api/v1/auth/users/$auth_user_id"

payload=$(printf \
    '{"name":"%s","credential_type":"password","username":"qa-user","secret":"%s"}' \
    "$credential_name" "$test_secret")
authorized_status=$(curl --silent --show-error --output "$response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$payload" "$base_url/api/v1/credentials")
credential_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$response_file")
asset_payload=$(printf \
    '{"name":"asset-integration-%s","host":"127.0.0.1","ssh_port":22,"os_family":"openeuler","environment":"test","group_name":"deployment","credential_id":"%s","labels":["integration"]}' \
    "$(date +%s)" "$credential_id")
asset_status=$(curl --silent --show-error --output "$asset_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$asset_payload" "$base_url/api/v1/assets")
asset_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$asset_response_file")
cmdb_marker="DEPLOY$(date +%s)"
cmdb_trace_id="deployment-cmdb-$cmdb_marker"
cmdb_business_status=$(curl --silent --show-error --output "$cmdb_business_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header "X-Request-ID: $cmdb_trace_id" --header 'Content-Type: application/json' \
    --data-binary "{\"code\":\"$cmdb_marker\",\"name\":\"Deployment CMDB $cmdb_marker\"}" \
    "$base_url/api/v1/cmdb/businesses")
cmdb_business_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$cmdb_business_file")
cmdb_application_status=$(curl --silent --show-error --output "$cmdb_application_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header "X-Request-ID: $cmdb_trace_id" --header 'Content-Type: application/json' \
    --data-binary "{\"business_id\":\"$cmdb_business_id\",\"code\":\"APP\",\"name\":\"Deployment Application\"}" \
    "$base_url/api/v1/cmdb/applications")
cmdb_application_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$cmdb_application_file")
cmdb_service_status=$(curl --silent --show-error --output "$cmdb_service_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header "X-Request-ID: $cmdb_trace_id" --header 'Content-Type: application/json' \
    --data-binary "{\"application_id\":\"$cmdb_application_id\",\"code\":\"API-SVC\",\"name\":\"Deployment Service\",\"service_type\":\"api\"}" \
    "$base_url/api/v1/cmdb/services")
cmdb_service_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$cmdb_service_file")
cmdb_api_status=$(curl --silent --show-error --output "$cmdb_api_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header "X-Request-ID: $cmdb_trace_id" --header 'Content-Type: application/json' \
    --data-binary "{\"service_id\":\"$cmdb_service_id\",\"code\":\"HEALTH\",\"name\":\"Health API\",\"http_method\":\"GET\",\"path\":\"/health\"}" \
    "$base_url/api/v1/cmdb/apis")
cmdb_relation_status=$(curl --silent --show-error --output "$cmdb_relation_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header "X-Request-ID: $cmdb_trace_id" --header 'Content-Type: application/json' \
    --data-binary "{\"asset_id\":\"$asset_id\",\"relation_role\":\"primary\"}" \
    "$base_url/api/v1/cmdb/services/$cmdb_service_id/assets")
cmdb_tree_status=$(curl --silent --show-error --output "$cmdb_tree_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/cmdb/tree?query=$cmdb_marker")
cmdb_audit_status=$(curl --silent --show-error --output "$cmdb_audit_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/audit-log?source=cmdb_change&page=1&page_size=20")
incident_status=$(curl --silent --show-error --output "$incident_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "{\"title\":\"Deployment incident\",\"description\":\"Verify immutable incident closure\",\"severity\":\"medium\",\"asset_id\":\"$asset_id\"}" \
    "$base_url/api/v1/incidents")
incident_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$incident_file")
for incident_state in acknowledged in_progress resolved closed; do
    incident_transition_status=$(curl --silent --show-error --output /dev/null \
        --write-out '%{http_code}' --max-time 5 --request PATCH \
        --header "Authorization: Bearer $API_ADMIN_TOKEN" \
        --header 'Content-Type: application/json' \
        --data-binary "{\"status\":\"$incident_state\",\"note\":\"Deployment verifier confirmed $incident_state\"}" \
        "$base_url/api/v1/incidents/$incident_id/status")
    [[ "$incident_transition_status" == "200" ]]
done
incident_timeline_status=$(curl --silent --show-error --output "$incident_timeline_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/incidents/$incident_id/timeline")
scan_payload=$(printf \
    '{"name":"scan-integration-%s","scan_type":"host_vulnerability","asset_ids":["%s"]}' \
    "$(date +%s)" "$asset_id")
scan_status=$(curl --silent --show-error --output "$scan_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$scan_payload" "$base_url/api/v1/scan-tasks")
schedule_payload=$(printf \
    '{"name":"schedule-integration-%s","scan_type":"host_vulnerability","asset_ids":["%s"],"schedule_kind":"interval","interval_hours":720,"timezone":"Asia/Shanghai"}' \
    "$(date +%s)" "$asset_id")
schedule_status=$(curl --silent --show-error --output "$schedule_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$schedule_payload" "$base_url/api/v1/scan-schedules")
schedule_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$schedule_response_file")
schedule_toggle_status=$(curl --silent --show-error --output "$schedule_toggle_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' --request PATCH \
    --data-binary '{"enabled":false}' \
    "$base_url/api/v1/scan-schedules/$schedule_id/enabled")
baseline_payload=$(printf \
    '{"name":"baseline-integration-%s","asset_ids":["%s"]}' \
    "$(date +%s)" "$asset_id")
baseline_status=$(curl --silent --show-error --output "$baseline_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$baseline_payload" "$base_url/api/v1/baselines")
baseline_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$baseline_response_file")
baseline_cancel_status=$(curl --silent --show-error --output "$baseline_cancel_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" --request POST \
    "$base_url/api/v1/baselines/$baseline_id/cancel")
exposure_payload=$(printf \
    '{"name":"exposure-integration-%s","asset_ids":["%s"]}' \
    "$(date +%s)" "$asset_id")
exposure_status=$(curl --silent --show-error --output "$exposure_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$exposure_payload" "$base_url/api/v1/exposures")
exposure_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$exposure_response_file")
exposure_cancel_status=$(curl --silent --show-error --output "$exposure_cancel_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" --request POST \
    "$base_url/api/v1/exposures/$exposure_id/cancel")
credential_risk_payload=$(printf \
    '{"name":"credential-risk-integration-%s","credential_ids":["%s"]}' \
    "$(date +%s)" "$credential_id")
credential_risk_status=$(curl --silent --show-error --output "$credential_risk_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$credential_risk_payload" "$base_url/api/v1/credential-risks")
credential_risk_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$credential_risk_response_file")
credential_risk_cancel_status=$(curl --silent --show-error --output "$credential_risk_cancel_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" --request POST \
    "$base_url/api/v1/credential-risks/$credential_risk_id/cancel")
knowledge_status=$(curl --silent --show-error --output "$knowledge_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/knowledge/sources")
coverage_status=$(curl --silent --show-error --output "$coverage_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/knowledge/coverage")
backfill_status=$(curl --silent --show-error --output "$backfill_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/knowledge/backfills")
vulnerability_status=$(curl --silent --show-error --output "$vulnerability_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/vulnerabilities?page=1&page_size=10")
dashboard_status=$(curl --silent --show-error --output "$dashboard_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/dashboard")
agent_status=$(curl --silent --show-error --output "$agent_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/agent")
agent_provider_status=$(curl --silent --show-error --output "$agent_provider_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/agent/providers/config")
report_status=$(curl --silent --show-error --output "$report_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/reports")
monitoring_status=$(curl --silent --show-error --output "$monitoring_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/monitoring/overview")
events_status=$(curl --silent --show-error --output "$events_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/events?page=1&page_size=10")
audit_log_status=$(curl --silent --show-error --output "$audit_log_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/audit-log?page=1&page_size=10")
patches_status=$(curl --silent --show-error --output "$patches_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/patches")
system_status=$(curl --silent --show-error --output "$system_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/system/status")
terminal_payload=$(printf '{"asset_id":"%s","operator":"admin"}' "$asset_id")
terminal_ticket_status=$(curl --silent --show-error --output "$terminal_ticket_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$terminal_payload" "$base_url/api/v1/terminal/tickets")
terminal_sessions_status=$(curl --silent --show-error --output "$terminal_sessions_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/terminal/sessions")
terminal_session_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$terminal_ticket_file")
terminal_assistant_payload=$(printf \
    '{"session_id":"%s","mode":"analysis_only","context":"service failed; password: deployment-verifier-secret","operator":"deployment-verifier"}' \
    "$terminal_session_id")
terminal_assistant_status=$(curl --silent --show-error --output "$terminal_assistant_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' \
    --data-binary "$terminal_assistant_payload" "$base_url/api/v1/terminal/assistant/analyze")
scan_id=$(.venv-wsl/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$scan_response_file")
curl --silent --show-error --output /dev/null --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" --request POST \
    "$base_url/api/v1/scan-tasks/$scan_id/cancel"
archive_status=$(curl --silent --show-error --output "$archive_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' --request PATCH \
    --data-binary '{"archived":true,"operator":"deployment-verifier"}' \
    "$base_url/api/v1/scan-tasks/$scan_id/archive")
lifecycle_status=$(curl --silent --show-error --output "$lifecycle_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    --header 'Content-Type: application/json' --request PATCH \
    --data-binary '{"status":"archived","reason":"部署验收数据归档","operator":"deployment-verifier"}' \
    "$base_url/api/v1/assets/$asset_id/lifecycle")

grep --quiet '"secret_mask":"\*\*\*\*\*\*\*\*"' "$response_file"
grep --quiet '"os_family":"openeuler"' "$asset_response_file"
grep --quiet "\"code\":\"$cmdb_marker\"" "$cmdb_tree_file"
grep --quiet "\"asset_id\":\"$asset_id\"" "$cmdb_tree_file"
grep --quiet '"http_method":"GET"' "$cmdb_tree_file"
grep --quiet "$cmdb_trace_id" "$cmdb_audit_file"
grep --quiet '"status":"closed"' "$incident_timeline_file"
grep --quiet '"status":"open"' "$incident_timeline_file"
if grep --quiet -i -E 'credential|secret|password|encrypted' \
    "$cmdb_business_file" "$cmdb_application_file" "$cmdb_service_file" \
    "$cmdb_api_file" "$cmdb_relation_file" "$cmdb_tree_file" "$cmdb_audit_file"; then
    echo "CMDB API exposed sensitive fields" >&2
    exit 1
fi
grep --quiet '"name":"CNVD"' "$knowledge_response_file"
grep --quiet '"sync_mode":"offline_package"' "$knowledge_response_file"
grep --quiet '"name":"openEuler Security Advisories"' "$knowledge_response_file"
grep --quiet '"sync_mode":"vendor_official_api"' "$knowledge_response_file"
grep --quiet '"is_complete":false' "$coverage_response_file"
grep --quiet '"publication_policy":"stage_validate_publish"' "$coverage_response_file"
grep --quiet '"failure_policy":"retain_active_release_and_alert"' "$coverage_response_file"
grep --quiet '"source_name":"CNVD"' "$coverage_response_file"
grep --quiet '"source_name":"openEuler Security Advisories"' "$coverage_response_file"
grep --quiet '^\[' "$backfill_response_file"
grep --quiet '"status":"queued"' "$scan_response_file"
grep --quiet '"read_only":true' "$scan_response_file"
grep --quiet '"schedule_kind":"interval"' "$schedule_response_file"
grep --quiet '"enabled":false' "$schedule_toggle_file"
grep --quiet '"read_only":true' "$baseline_response_file"
grep --quiet '"policy_version":"2026.09"' "$baseline_response_file"
grep --quiet '"status":"cancelled"' "$baseline_cancel_file"
grep --quiet '"read_only":true' "$exposure_response_file"
grep --quiet '"methodology_version":"2026.09"' "$exposure_response_file"
grep --quiet '"status":"cancelled"' "$exposure_cancel_file"
grep --quiet '"offline_only":true' "$credential_risk_response_file"
grep --quiet '"policy_version":"2026.09"' "$credential_risk_response_file"
grep --quiet '"status":"cancelled"' "$credential_risk_cancel_file"
if grep --quiet "$test_secret" "$credential_risk_response_file" "$credential_risk_cancel_file"; then
    echo "Credential risk API returned plaintext secret" >&2
    exit 1
fi
grep --quiet '"summary"' "$vulnerability_response_file"
grep --quiet '"disposition_status"' "$vulnerability_response_file" || grep --quiet '"items":\[\]' "$vulnerability_response_file"
grep --quiet '"recent_scans"' "$dashboard_response_file"
grep --quiet '"affected_assets"' "$dashboard_response_file"
grep --quiet '"monitoring_mode":"ssh_agentless_near_realtime"' "$dashboard_response_file"
grep --quiet '"monitoring_assets"' "$dashboard_response_file"
grep --quiet '"resource_alerts"' "$dashboard_response_file"
grep --quiet '"id":"local-rules"' "$agent_response_file"
grep --quiet '"id":"deepseek"' "$agent_provider_response_file"
if grep --quiet -E 'deepseek-test-key|"api_key":|"encrypted_api_key":|"secret":' "$agent_provider_response_file"; then
    echo "AI provider API exposed plaintext key" >&2
    exit 1
fi
grep --quiet '"recommendations"' "$agent_response_file"
grep --quiet '^\[' "$report_response_file"
grep --quiet '"summary"' "$monitoring_response_file"
grep --quiet '"items"' "$events_response_file"
grep --quiet '"total"' "$events_response_file"
grep --quiet '"items"' "$audit_log_response_file"
grep --quiet '"total"' "$audit_log_response_file"
provider_audit_status=$(curl --silent --show-error --output "$provider_audit_response_file" \
    --write-out '%{http_code}' --max-time 5 \
    --header "Authorization: Bearer $API_ADMIN_TOKEN" \
    "$base_url/api/v1/audit-log?source=ai_provider_config")
[[ "$provider_audit_status" == "200" ]]
grep --quiet '"items"' "$provider_audit_response_file"
grep --quiet '"total"' "$provider_audit_response_file"
grep --quiet '^\[' "$patches_response_file"
grep --quiet '"execution_enabled":false' "$patches_response_file" || [[ "$(tr -d '[:space:]' < "$patches_response_file")" == "[]" ]]
grep --quiet '"database_status":"ok"' "$system_response_file"
grep --quiet '"schema_revision":"20260918_24"' "$system_response_file"
grep --quiet '"authentication":"password_session"' "$system_response_file"
grep --quiet '"host_key_verification":true' "$system_response_file"
grep --quiet '"scan_read_only_default":true' "$system_response_file"
grep --quiet '"ai_direct_execution":false' "$system_response_file"
if grep --quiet -E '"secret"|"password"|"execute"|"dispatch"' "$patches_response_file"; then
    echo "Patch API exposed sensitive or execution fields" >&2
    exit 1
fi
if grep --quiet -E '"secret"|"redacted_context"' "$audit_log_response_file"; then
    echo "Unified audit API exposed sensitive fields" >&2
    exit 1
fi
grep --quiet '"websocket_path":"/api/v1/terminal/ws"' "$terminal_ticket_file"
grep --quiet '"asset_id"' "$terminal_sessions_file"
grep --quiet '"mode":"analysis_only"' "$terminal_assistant_file"
grep --quiet '"commands":\[\]' "$terminal_assistant_file"
grep --quiet '\[REDACTED\]' "$terminal_assistant_file"
if grep --quiet 'deployment-verifier-secret' "$terminal_assistant_file"; then
    echo "Terminal assistant returned unredacted context" >&2
    exit 1
fi
grep --quiet '"archived":true' "$archive_file"
grep --quiet '"lifecycle_status":"archived"' "$lifecycle_file"
if grep --quiet -i 'credential\|secret\|password' "$terminal_sessions_file"; then
    echo "Terminal audit exposed credential data" >&2
    exit 1
fi
if docker compose --env-file deploy/.env exec -T \
    -e "TEST_SECRET=$test_secret" postgres sh -c \
    'grep -a -R -q "$TEST_SECRET" /var/lib/postgresql/data 2>/dev/null'; then
    echo "Plaintext secret found in PostgreSQL storage" >&2
    exit 1
fi

migration=$(docker compose --env-file deploy/.env exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    'select version_num from alembic_version;')

[[ "$page_status" == "200" ]]
[[ "$health_status" == "200" ]]
[[ "$unauthorized_status" == "401" ]]
[[ "$auth_create_status" == "200" || "$auth_create_status" == "201" ]]
[[ "$auth_login_status" == "200" ]]
[[ "$auth_me_status" == "200" ]]
[[ "$auth_logout_status" == "204" ]]
[[ "$authorized_status" == "201" ]]
[[ "$asset_status" == "201" ]]
[[ "$cmdb_business_status" == "201" ]]
[[ "$cmdb_application_status" == "201" ]]
[[ "$cmdb_service_status" == "201" ]]
[[ "$cmdb_api_status" == "201" ]]
[[ "$cmdb_relation_status" == "201" ]]
[[ "$cmdb_tree_status" == "200" ]]
[[ "$cmdb_audit_status" == "200" ]]
[[ "$incident_status" == "201" ]]
[[ "$incident_timeline_status" == "200" ]]
[[ "$knowledge_status" == "200" ]]
[[ "$coverage_status" == "200" ]]
[[ "$scan_status" == "201" ]]
[[ "$schedule_status" == "201" ]]
[[ "$schedule_toggle_status" == "200" ]]
[[ "$baseline_status" == "201" ]]
[[ "$baseline_cancel_status" == "200" ]]
[[ "$exposure_status" == "201" ]]
[[ "$exposure_cancel_status" == "200" ]]
[[ "$credential_risk_status" == "201" ]]
[[ "$credential_risk_cancel_status" == "200" ]]
[[ "$vulnerability_status" == "200" ]]
[[ "$dashboard_status" == "200" ]]
[[ "$agent_status" == "200" ]]
[[ "$agent_provider_status" == "200" ]]
[[ "$backfill_status" == "200" ]]
[[ "$report_status" == "200" ]]
[[ "$monitoring_status" == "200" ]]
[[ "$events_status" == "200" ]]
[[ "$audit_log_status" == "200" ]]
[[ "$patches_status" == "200" ]]
[[ "$system_status" == "200" ]]
[[ "$terminal_ticket_status" == "201" ]]
[[ "$terminal_sessions_status" == "200" ]]
[[ "$terminal_assistant_status" == "201" ]]
[[ "$archive_status" == "200" ]]
[[ "$lifecycle_status" == "200" ]]
[[ "$migration" == "20260918_24" ]]

printf 'page=%s health=%s unauthorized=%s password_login=%s session_me=%s logout=%s credential_create=%s asset_create=%s cmdb_business=%s cmdb_application=%s cmdb_service=%s cmdb_api=%s cmdb_asset_bind=%s cmdb_tree=%s cmdb_audit=%s incident_create=%s incident_timeline=%s scan_create=%s schedule_create=%s schedule_disable=%s baseline_create=%s baseline_cancel=%s exposure_create=%s exposure_cancel=%s credential_risk_create=%s credential_risk_cancel=%s knowledge=%s coverage=%s backfills=%s vulnerabilities=%s dashboard=%s agent=%s reports=%s monitoring=%s events=%s audit_log=%s patches=%s system=%s terminal_ticket=%s terminal_sessions=%s terminal_assistant=%s scan_archive=%s asset_archive=%s masked=yes plaintext_in_database=no migration=%s\n' \
    "$page_status" "$health_status" "$unauthorized_status" "$auth_login_status" "$auth_me_status" "$auth_logout_status" \
    "$authorized_status" "$asset_status" "$cmdb_business_status" "$cmdb_application_status" "$cmdb_service_status" "$cmdb_api_status" "$cmdb_relation_status" "$cmdb_tree_status" "$cmdb_audit_status" "$incident_status" "$incident_timeline_status" "$scan_status" "$schedule_status" "$schedule_toggle_status" "$baseline_status" "$baseline_cancel_status" "$exposure_status" "$exposure_cancel_status" "$credential_risk_status" "$credential_risk_cancel_status" "$knowledge_status" "$coverage_status" "$backfill_status" \
    "$vulnerability_status" "$dashboard_status" "$agent_status" "$report_status" "$monitoring_status" "$events_status" "$audit_log_status" "$patches_status" "$system_status" \
    "$terminal_ticket_status" "$terminal_sessions_status" "$terminal_assistant_status" "$archive_status" "$lifecycle_status" "$migration"
