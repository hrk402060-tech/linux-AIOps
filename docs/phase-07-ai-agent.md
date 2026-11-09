# Phase 07: AI Agent

## Administrator Configuration (Published 2026-10-14)

Model administration moves to System Management on the existing system page. Only administrators can read or update configuration; ordinary AI usage retains its existing role checks. Operations chat and vulnerability triage have separate configuration sections. The vulnerability external providers remain unavailable for inference; saved credentials or a successful local decryption check do not establish model connectivity.

GET/PUT `/api/v1/agent/settings/terminal` manages operations-chat `enabled`, `base_url`, `model`, `timeout_seconds` (5..120), optional `api_key`, and `clear_api_key`. API keys use the existing AES-256-GCM vault and fixed response masks; audit identifies the authenticated operator without recording keys or terminal evidence. Empty keys retain existing material; explicit clearing and a new key are mutually exclusive. Changing an endpoint requires a new key or explicit clearing so an old key is not sent to another endpoint. Configuration is read from the database for each conversation and takes effect on the next request.

Legacy `TERMINAL_AI_*` environment values are not imported or used as fallback. On release, an administrator must enter the approved endpoint/model/key in the interface; until then, or when disabled, operations chat uses local rules. The server-only master encryption key remains a deployment secret. HTTP(S) private/local model endpoints are supported, without URL credentials, query parameters, fragments or redirects; TLS verification is retained and environment proxies are not used. Configure only trusted endpoints: model use sends redacted conversation evidence to that selected endpoint, and configuration saves do not test external connectivity.

Production migration `20261013_32` adds three configuration columns after `20261012_31`, preserving existing encrypted vulnerability-provider keys. Workspace-only merge `20261013_34` is not a production release artifact. No AI write permission, tool dispatch or automatic repair is introduced. Published at `http://192.168.10.17:8088/` with the security-check tabs; the 44-file manifest, HTTP assets, configuration, existing provider ciphertext/audits and protected business data passed release verification.

The initial configuration release exposed an existing 16.36-second workspace query, exceeding the frontend's 10-second timeout. The queue optimization below resolves that measured production latency without increasing frontend timeouts. External-provider connectivity, browser visual acceptance and mobile were not verified.

## Queue Query Optimization (Published 2026-10-14)

The release materializes the latest finding IDs on PostgreSQL before applying the existing relationship and disposition rules. Summary counts share one aggregate query, and page rows include case status directly, keeping workspace reads at four queries rather than one query per finding. The API, roles, pagination, human review and no-execution boundary are unchanged; no schema migration or frontend timeout increase is required.

Targeted backend tests pass (34 cases). Read-only profiling on the existing 7,902 findings reduced workspace query time from 31.145 seconds to 0.088 seconds and matched an independent latest-evidence reference. Disposable PostgreSQL HTTP tests with 7,896 synthetic findings returned three pages in about 0.08 seconds, including duplicate-advice aggregation and empty-page global metrics.

The two-file patch was published at `http://192.168.10.17:8088/`. Production workspace HTTP checks returned the first page in 0.095 seconds, the second in 0.086 seconds and an empty page in 0.073 seconds. Eight authenticated GETs, anonymous rejection, full metrics, masks and no-execution responses passed. The original schema32, frontend artifacts, configuration, provider ciphertext/audits and protected business data were retained; only the existing backend container restarted. This is release verification, not browser acceptance or a claim that every platform feature was tested.

The AI Agent is an in-domain triage workspace backed by a pluggable provider interface. The current release enables only the deterministic local rules provider. It derives risk analysis, remediation guidance, validation steps, rollback requirements, and duty-alert priority from immutable vulnerability evidence.

Recommendations are persisted with their evidence snapshot and review state. Human approval records acceptance of the advice only: the API always returns `can_execute: false`, and no command-dispatch endpoint exists. External providers remain disabled for inference; existing administrator-only API-key storage is encrypted and masked, and validation does not make an external request.

## Queue And Review Governance

`GET /api/v1/agent` ranks immutable findings by asset and CVE before applying disposition filters. Only the latest evidence for open, pending-verification or expired risk-acceptance cases is eligible. Archived assets and inconsistent or missing relationships are excluded; disabled assets remain available for human evidence analysis without collection. Expired acceptance is projected as open without changing evidence or disposition audit from this read endpoint.

The endpoint accepts `queue_page`, `queue_page_size`, `history_page` and `history_page_size`. Pages start at 1; both page sizes default to 50 and are capped at 50. Queue totals and metrics cover the full eligible dataset, while history totals cover all retained recommendations, including findings no longer eligible. Historical advice is explicitly global, not a complete per-finding timeline inferred from a loaded page.

Triage rejects superseded or ineligible evidence with HTTP 409 and rechecks eligibility before persistence. Review atomically advances `pending_review` to `approved` or `rejected` once, recording the authenticated username and timestamp together. Repeat or concurrent losing requests receive HTTP 409 without replacing the original review. Migration `20261004_29` adds nullable `reviewed_by`; legacy reviewer identities remain unknown. Approval never grants execution permission.

Administrators and operators may generate and review advice; auditors are read-only. Provider-key writes remain administrator-only. The frontend refreshes authoritative counts and selections after writes or conflicts and ignores obsolete refresh responses. No SSH action, external inference or deployment is introduced by this governance update.
