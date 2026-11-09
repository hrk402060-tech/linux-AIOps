# Project Context

## Diagnostic Authorization Asset Boundary (Released 2026-10-24)

Diagnostic authorization listings and audit history use the caller's visible
asset set. Manually requested alert diagnostics retain the requesting username
and recheck that user's current enabled role, temporary-password state, and
asset assignment before queue claim, during the lease guard, and before result
finalization. Revoked access cannot continue collection or produce a successful
outcome; automatic system-triggered diagnostics remain governed by the
dedicated read-only grant.

An alert/job asset mismatch is sealed before any connection. Unified audit
output filters diagnostic authorization records for scoped users, while
unmapped-alert notifications keep their existing global operational behavior.
This release changes only eight backend authorization files and tests; it adds
no schema, UI, credential, or production configuration change. It was published
to `http://192.168.10.17:8088/` after isolated Docker verification (`10/10`
scope tests, `72/72` diagnostic/alert/recovery regression, Ruff and compileall).
Production remains on schema `20261024_44`; frontend and PostgreSQL were not
restarted. Rollback material is stored at
`/opt/linux-ops-platform/.backup/release-diagnostic-scope-20261024224641`.

## API Probe Authorization Boundary

Probe reads and actions use the caller's current visible asset set. Assigned
users need an active link between that exact asset and service, and an optional
API must belong to that service. Shared service visibility grants neither access
to another asset's probes nor permission to edit shared CMDB metadata.

The same SQL scope filters counts, pagination and latest results. Historical
runs independently validate their asset, service, API, application and business
context. Empty scopes, revoked grants and unlinked relations fail closed.
Disabled assets retain authorized history but cannot be probed. Existing
administrator CIDR policy, target-IP checks, sensitive-address blocking,
GET/HEAD-only transport and auditor read-only restrictions remain unchanged.
No schema, endpoint or frontend change is required; release status remains in
the authoritative workspace context.

## Service Fingerprint Enhancement (Candidate 2026-10-24)

Service vulnerability scans now retain a bounded Banner read from the target's
loopback TCP listener when Bash and `timeout` are available. The probe is
read-only, capped at 512 bytes and 16 TCP listeners per asset, normalized to
256 printable characters, and never probes UDP or non-numeric listeners.
Failure to read a Banner does not fail the scan and does not create a finding
by itself.

Knowledge packages may optionally declare `service_banner_keywords` for an
affected package. A service finding still requires the existing listener,
process, executable, installed-package, and vendor-advisory evidence chain;
keywords only narrow a mapped service match. A Banner can never confirm a CVE
on its own, and existing backport/manual-confirmation rules remain unchanged.
The task details and vulnerability evidence drawer expose the bounded service
fingerprint. This candidate adds no table, migration, permission, or write
operation and is not published until PM explicitly requests release.

## Current Architecture (2026-10-15)

The authoritative release/development status remains in `../../.codex/project.md`. Durable diagnostic jobs and recovery verification are now released on production schema36. The earlier candidate/planned labels below are historical.

The released K8s connector extends the existing alert and diagnostic-authorization tabs without adding menus. Production is on schema37. One asset has one transport-specific authorization and a monotonically increasing revision; existing Linux grants default to `linux`. K8s uses a separate cluster-mapping asset with no manual SSH credential and cannot convert an existing Linux grant. The diagnostic queue, cancellation, leases, immutable outcomes and mandatory notification outbox are shared, not duplicated.

This first connector only reads scoped Pods, Deployments, StatefulSets, DaemonSets and core Events. It uses fixed namespaced GET requests, pinned literal-IP HTTPS with an administrator-supplied CA, and dedicated ServiceAccount tokens encrypted together with their CA. API identity and permissions are enforced by Kubernetes; local JWT subject/expiry checks and administrator RBAC confirmation are not signature verification or proof that the identity has no extra privileges. Secrets, raw configuration, logs, nodes, exec, proxy, watch and all writes are unavailable. Only reviewed status projections reach evidence; resource messages, annotations, environment values and raw responses do not.

The total operation, each request, pagination, items, bytes and evidence size have hard limits. Every page/read rechecks grant revision, expiry, lifecycle and queue fencing. Incomplete results are explicitly partial/failed. Configuration and errors never return tokens, CA contents or encrypted values. Changing target, identity or CA requires a different token, not resubmission of the retained secret. Migration37 is additive over36; downgrade is refused once any K8s authorization exists to prevent loss of transport metadata. The release is published at `http://192.168.10.17:8088/`; actual cluster access, SMTP delivery and browser visual acceptance still require separate verification.

## Current Product Boundary (2026-10-03)

The authoritative scope is maintained in `../../.codex/project.md`. Current delivery focuses on alert reception, diagnosis, mandatory administrator notifications, human handling, verified recovery and retained records, plus the separate vulnerability subsystem. The AI is strictly read-only in every path; human approval cannot grant it write or dispatch permissions. Earlier controlled-execution roadmap entries below are historical and superseded.

The implementation closes legacy terminal AI execution (including approved records and WebSocket dispatch). Missing dedicated diagnostic authorization blocks collection before asset credential access or SSH. The existing alert detail exposes reception, diagnosis, notification, human handling, recovery verification and records. The released notification implementation described below supports SMTP delivery once administrators configure the channel; recovery verification, Docker and Kubernetes connectors are released with the same read-only boundary. Linux observation policies do not replace restricted target identities, and real target authorization remains an operational prerequisite.

The isolated `/preview` SQLite seed refuses non-preview paths, traversal and existing databases. Examples are explicitly labelled as demonstrations and contain no real target credentials or collected evidence. Testing and preview run only in isolated Docker on `192.168.10.17`; production changes require a separate explicit PM release instruction.

### Durable Diagnostic Jobs (Candidate 2026-10-09)

Alert ingestion now commits its receipt, receive notification, diagnostic job and timeline without SSH. Each job binds the asset and dedicated authorization revision at admission; absent authorization records a failed outcome immediately rather than waiting for future permission. A database admission lock bounds 500 active jobs and two shared slots enforce global concurrency across processes. Ninety-second leases fence every connection/action/read and finalization against stale workers.

At most three attempts retry only trusted transient transport failures or interrupted workers. Every attempt has separate immutable evidence, grant read audit and mandatory result outbox. Running cancellation requests interrupt reads but retain the slot until transport cleanup or fenced lease recovery; cancellation never deletes evidence or claims no execution after an interrupted read. Actual admin/operator sessions, exact alert-ID confirmation and a reason are mandatory for enqueue/cancel; auditors and break-glass tokens cannot write.

The existing alert page displays current state, retry time, cancellation pending and bounded job/attempt history. Attempts filter by job_id before server pagination. Migration31 adds jobs/admission/slots and nullable analysis links, preserving old evidence/notification links. This section describes the historical queue candidate; the current production release is documented at the top of this file. Kubernetes/Docker connectors and recovery verification are later released stages, while browser visual acceptance remains a separate requirement.

### Dedicated Linux Diagnostics (Released 2026-10-07)

The existing activity-alert page adds a dedicated diagnostic-authorization tab, not a new menu or PageKey. Only actual administrator sessions may grant/revoke scoped, expiring authorization; private keys have separate AES-GCM storage and never reuse manual SSH credentials. The Linux connector accepts only a pinned host and administrator-installed non-root forced-command gateway with six finite observation actions. Revalidation interrupts further reads on revoke, revision, expiry, lifecycle or target/key changes. Previous reads and attempted grant revisions remain evidence; partial/failed outcomes still enter administrator notification outbox. Gateway protocol is not proof of target permissions; installation and independent denial checks remain the target administrator's responsibility.

This stage's separate Docker preview contained observations from a temporary isolated non-root gateway, explicitly labelled DEMO and never production evidence. Kubernetes, Docker, durable diagnostic jobs and recovery verification were delivered in later releases; this historical stage itself only covered the Linux connector. PM authorized publication on October 7; production migration30, bounded file/resource hashes, data/configuration preservation and authenticated read-only checks passed at `http://192.168.10.17:8088/`. Browser visual acceptance remains unverified because tooling security policy blocked access; publication is not a claim of visual acceptance. Installation/API contracts are in `deployment.md`; detailed test and delivery state is maintained only in the authoritative workspace project context.

### Administrator Notifications (Released 2026-10-03)

The existing alert page gains SMTP configuration and delivery history. Receipt and diagnostic-result notifications are enqueued transactionally with alert evidence, including failed/unauthorized diagnosis. Valid signed alerts that cannot map to an enabled asset retain a separate blocked-mapping notification and replay receipt; they never trigger arbitrary-target SSH. TLS certificate validation is mandatory. SMTP passwords use the existing AES-256-GCM vault and are never returned to the browser.

The outbox uses a unique event key, bounded batches, conditional lease claims, interrupted-worker recovery and five total attempts with exponential backoff. Missing/disabled configuration retains blocked rows without consuming send attempts. Stable Message-ID reduces duplicate handling but cannot guarantee exactly once: SMTP acceptance followed by a database crash, or partial recipient refusal, can cause repeat deliveries. Terminal failures need human escalation; no secondary channel is claimed.

Config and retry require system-admin privileges. Acknowledgment requires a real admin/operator login session; break-glass Bearer and auditor cannot mark a message viewed. Delivery success only means all intended recipients were accepted by SMTP, not reading, confirmed root cause or verified recovery. Historical alert evidence is not backfilled silently. Released to `http://192.168.10.17:8088/`; production checks passed and the channel still requires SMTP/recipient configuration. Detailed release evidence is maintained only in the authoritative project context.

API: GET/PATCH `/api/v1/alert-notification-config`; GET `/api/v1/alert-notifications?offset=0&limit=50`; GET `/api/v1/alerts/{id}/notifications`; POST `/api/v1/alert-notifications/{id}/retry`; POST `/api/v1/alert-notifications/{id}/acknowledge`. History is bounded to 100 rows per alert and 100 per global page. Public records expose safe correlation fields, never authentication secrets, raw mail bodies or worker claim tokens. Migration `20261003_28` adds configuration, outbox, nonce receipts and audit tables over `20260925_27`.

## Identity

- Name: `app-linux-vuln-sentinel`
- Chinese name: Linux 智能综合运维平台
- Platform positioning: Linux Intelligent Integrated Operations Platform. Operations is the primary product line; security is a subsystem.
- Current domain: Security Operations Center. The existing application remains intact inside this center.
- Type: Full-stack Linux intelligent integrated operations platform; current delivery focus is the Security Operations Center
- Target environment: Linux
- Status: Linux security operations platform MVP implemented and verified with Docker Compose

## Platform Domain Map

The formal Linux platform contains twelve centers:

1. Unified Operations Workspace
2. Service Catalog and CMDB
3. Monitoring and Observability
4. Alerting and Intelligent Early Warning
5. Incident, Ticket, and SLO
6. Automation and Inspection
7. Release, Configuration, and Change
8. Backup and Disaster Recovery
9. Network, Domain, and Certificate
10. Security Operations
11. Integration, Agent, and Cloud Resources
12. Platform Administration

Windows support remains out of scope. New operations capabilities are added as a subsystem, secondary page, shared service, or connector within these centers unless an architecture review proves that no existing boundary applies.

The existing `app-linux-vuln-sentinel` implementation belongs to Security Operations. Its asset, credential, task, audit, Web SSH, knowledge, and AI capabilities remain usable while shared responsibilities are extracted behind stable contracts. Existing data and working behavior will not be discarded or rewritten.

## Unified Operations Model

The platform business loop is: business and service modeling; metric, log, and trace collection; synthetic checks, alerts, and trend prediction; impact analysis; incident and ticket handling; AI-assisted root cause analysis; human confirmation or approval; controlled automation; monitoring and synthetic verification; postmortem, audit, and knowledge capture.

All new persistence and API designs must preserve the relevant identifiers: `business_id`, `application_id`, `service_id`, `api_id`, `asset_id`, `alert_id`, `incident_id`, `ticket_id`, `task_id`, `change_id`, `deployment_id`, and `trace_id`. Every feature review must cover CMDB relationships, unified identity and authorization, correlation identifiers, alerts, tasks, audit, retention, and deployment impact.

## Delivery Contract

Implementation proceeded through staged page and capability deliveries. Following the PM instruction on September 15, 2026, development continued across remaining approved Linux platform modules without stopping at individual page gates; final validation is performed at platform level.

## Mandatory Architecture Decisions

### Vulnerability Decisions

Vendor security advisories are authoritative. Generic version ranges are supporting evidence only. When a vendor backport cannot be inferred reliably, the finding is marked as requiring manual confirmation. Every conclusion carries an evidence chain and deterministic confidence level.

### Domestic Intelligence Sources

CNVD and CNNVD use signed offline packages as the primary ingestion path. Signature, trusted issuer, manifest, and SHA-256 validation occur before staging. Any failure rejects the complete import. Online sources remain secondary and cannot independently create a confirmed finding.

### Credential Protection

SSH secrets use AES-256-GCM authenticated encryption with a unique nonce per record and contextual AAD. Encryption keys remain outside the database in a server-side secret manager. Browser APIs return identifiers, metadata, and masked values only.

### Scan State Machine

```text
QUEUED -> SCANNING -> AGGREGATING -> COMPLETED
                     |              -> FAILED
                     +--------------> CANCELLED
```

A rerun creates a new task linked to its parent and never overwrites historical evidence. Scans are read-only and non-destructive by default.

### Knowledge Base Releases

Imported data enters staging before validation and approval. Production knowledge is published as an immutable version, can be rolled back in one action, and exposes source health, synchronization progress, counts, duration, and errors. Every scan records its knowledge base version.

### AI Agent Boundary

The vulnerability AI remains scoped to security triage and recommendations. All AI assistants are strictly read-only: human approval cannot enable execution, and AI output never enters the manual SSH command channel. Dedicated diagnosis identities, scoped structured tools and sanitized evidence are required for future collection. Deployment-configured model providers retain their existing configuration; no new external data flow is introduced by this stage.

## Roles

- PM: requirements and acceptance, owned by the user
- Project Manager: progress, risks, and stage boundaries
- Architect: architecture review and written decisions
- UI Designer: seven-page interaction and visual design
- Frontend Developer: React and Ant Design implementation
- Backend Developer: API, worker, knowledge base, and persistence
- QA Engineer: test design and stage validation
- DevOps Engineer: active after the PM requested Docker deployment
- Deployment baseline: Linux Docker Engine with Docker Compose; React/Nginx, FastAPI, and PostgreSQL migrate together as one stack

## Stage 4 Architecture Review: Scan Tasks

- Review result: approved. Scan orchestration is separated from the Vulnerability Details page and preserves immutable run history.
- State machine: `queued -> scanning -> aggregating -> completed/failed/cancelled`; reruns create a new task linked through `rerun_of_id`.
- Evidence: each finding stores source, advisory, installed version, fixed version, conclusion, and confidence. Vendor advisories can produce confirmed findings; uncertain backports and supplemental sources require manual confirmation.
- Safety boundary: SSH collection is read-only and does not elevate privileges, write remote files, install packages, exploit vulnerabilities, or apply remediation. A task fails when no selected asset can be collected, preventing a false zero-finding success.
- Reports: completed runs expose an auditable text report and retain the asset and knowledge-release snapshots used by the scan.
- Current limitation: service scans inventory listening services; banner-specific service CVE matching is deferred to a later scanner capability.
- Delivery verification: Ruff, 28 backend tests, 8 structure tests, 12 frontend tests, TypeScript/Vite build, SQLite and PostgreSQL migrations, Docker end-to-end verification, and desktop/mobile visual checks passed.
- Stage gate: Stage 5 Vulnerability Details must not begin before PM accepts the Scan Tasks page.

## Stage 6 Architecture Review: Dashboard

- Review result: approved. The dashboard is a read-only operational projection and does not duplicate or mutate source records.
- API boundary: `GET /api/v1/dashboard` returns asset health, vulnerability severity and conclusions, scan health, active knowledge release, recent scans, and the most affected assets in one bounded response.
- UI boundary: the first screen supports rapid triage and routes operators to Scan Tasks, Vulnerability Details, and Knowledge Base for focused work.
- Data integrity: all counts are calculated from normalized assets, immutable findings, scan history, and published knowledge releases. No new table or migration is required.
- Safety: the page exposes no remediation or AI execution action. Scans remain read-only and manual-confirmation findings remain visually distinct.
- Delivery verification: Ruff, 32 backend tests, 8 structure tests, 17 frontend tests, TypeScript/Vite build, PostgreSQL migration, Docker end-to-end verification, and desktop/mobile visual checks passed.
- Stage gate: Stage 7 AI Agent must not begin before PM accepts the Dashboard page.

## Stage 7 Architecture Review: AI Agent

- Review result: approved. AI triage is isolated behind a provider interface; the current provider is deterministic and runs locally.
- Data boundary: analysis uses stored vulnerability evidence only. No finding, asset data, credential, or model prompt leaves the deployment domain.
- Advice lifecycle: generated recommendations preserve an evidence snapshot and transition from `pending_review` to `approved` or `rejected`.
- Execution boundary: approval accepts the recommendation but never executes it. The API exposes `can_execute: false` and has no remediation-dispatch endpoint.
- Extensibility: external providers and encrypted per-provider API keys may be added later without changing the recommendation contract; they are disabled in this release.
- Delivery verification: Ruff, 35 backend tests, 8 structure tests, 19 frontend tests, TypeScript/Vite build, SQLite and PostgreSQL migrations, and Docker end-to-end verification passed.
- Verification limitation: automated desktop/mobile screenshots were blank because local browser automation was restricted; PM visual acceptance remains required.

## Stage 8 Architecture Review: Vulnerability Lifecycle

- Review result: approved. Immutable scan findings are separated from mutable vulnerability-case status and append-only audit history.
- Lifecycle: operators can record open, pending verification, ignored, or time-bounded risk acceptance; only a completed read-only rescan can mark a case fixed.
- Verification evidence: a fixed case records the verification scan, observed remediated version, fixed time, and all prior scan and knowledge-version snapshots.
- Query contract: the default list returns active cases and folds repeated scans by asset and CVE; exact asset ID, severity, conclusion, and disposition filters can be combined.
- Deletion boundary: normal business UI cannot physically delete findings. The administrator endpoint accepts only test cleanup or compliance expiry purposes and retains a deletion audit snapshot.
- Stage gate: Knowledge Base coverage transparency must not begin before PM accepts this Vulnerability Details update.
- Delivery verification: Ruff, 40 backend tests, 8 structure tests, 22 frontend tests, TypeScript/Vite build, historical-data migration, PostgreSQL migration, and Docker end-to-end verification passed.

## Stage 9 Architecture Review: Knowledge Coverage Transparency

- Review result: approved. Coverage is computed from the active immutable release and synchronization history; it does not claim the configured source list is complete vulnerability coverage.
- Visibility: every configured distribution or source exposes advisory count, latest advisory, last successful synchronization, mode, health, and stale status, including sources with no published data.
- Scheduling policy: configured online vendor sources show a recommended daily 02:00 synchronization and four-hour critical-intelligence checks. CNVD, CNNVD, and sources without stable APIs remain signed offline-package sources.
- Publication safety: packages are signature-checked and structurally validated before activation. A failed update retains the prior active production release and raises a source health alert.
- Decision authority: vendor advisories remain authoritative; uncertain vendor backports continue to produce `manual_confirmation` findings.
- Stage gate: stop after Knowledge Base delivery for PM acceptance before opening another page stage.
- Delivery verification: Ruff, 42 backend tests, 8 structure tests, 23 frontend tests, TypeScript/Vite build, PostgreSQL migration, and Docker end-to-end verification passed.

## Stage 10 Architecture Review: Linux Service Vulnerability Detection

- Review result: approved. Service findings require a read-only evidence path from listening socket to process, executable, installed package, native package version comparison, and matching vendor advisory.
- False-positive boundary: a port number, process name, or banner is not sufficient to confirm a vulnerability. An unmapped service produces inventory evidence only and no fabricated vulnerability finding.
- Result integrity: multiple sockets owned by the same affected package are consolidated into one asset and CVE result, while all listening endpoints remain in its evidence.
- Safety: collection uses SSH read-only commands and never elevates privileges, writes remote files, probes an exploit, or changes a service.
- Known limitation: an unprivileged account may not see process metadata owned by another user. Those services remain visibly unmapped and are not assessed until the account has normal read access.
- Stage gate: stop after the Scan Tasks page enhancement for PM acceptance before opening another page stage.
- Delivery verification: Ruff, 45 backend tests, 8 structure tests, 24 frontend tests, TypeScript/Vite build, PostgreSQL migration, and Docker end-to-end verification passed.

## Stage 11 Architecture Review: openEuler Official Intelligence

- Review result: approved. The adapter is restricted to the openEuler official HTTPS API and validates response size, structure, CVE identifiers, advisory identity, product release, and fixed RPM evidence before publication.
- Publication model: fetched data is staged in memory, validated completely, merged by advisory identifier, and published as an immutable release in one database transaction. Failure retains the previous production release and records a source alert.
- Accuracy boundary: each fixed package records its applicable openEuler release and service pack. Scans skip announcements for a different product release, and native RPM comparison remains the final version check.
- Scheduling: synchronization aligns to 02:00 Asia/Shanghai and repeats every four hours for critical-intelligence checks. Manual synchronization uses the same validation and publication path.
- Coverage disclosure: this stage ingests the newest bounded batch incrementally and does not claim historical full coverage. CNVD/CNNVD and sources without stable APIs remain signed offline packages.
- Stage gate: stop after the Knowledge Base page update for PM acceptance before connecting another distribution.
- Delivery verification: Ruff, 51 backend tests plus the final targeted product-version test, 8 structure tests, 25 frontend tests, TypeScript/Vite build, PostgreSQL migration, Docker end-to-end verification, and a live official-feed publication passed. The active release contains 346 openEuler CVE records from the latest 20 official advisories, with 2026-09-05 as the newest advisory date.

## Stage 12 Architecture Review: openEuler Historical Backfill

- Review result: approved. Historical ingestion is a persistent `queued -> running -> completed/failed/cancelled` job with a durable page cursor.
- Resumability: the cursor advances only after a complete page validates and publishes. Failures and service restarts retain the current page and expose a resume action.
- Concurrency: only one historical job may be active, and scheduled openEuler synchronization skips while it runs.
- Operations: the Knowledge Base page shows page and announcement totals, published CVE records, percentage, errors, cancel, and resume controls.
- Publication safety: every page uses the existing stage-validate-publish path; failures retain the active production release. Findings continue to use vendor advisory authority and manual confirmation for uncertain backports.
- Scope boundary: other distribution feeds and domestic signed-package imports remain deferred by PM decision. No full live backfill is started automatically.
- Stage gate: stop after this Knowledge Base enhancement for PM acceptance before starting the Reports page.
- Delivery verification: Ruff, 56 backend tests, 8 structure tests, 26 frontend tests, TypeScript/Vite build, PostgreSQL migration `20260907_08`, and Docker end-to-end verification passed. Three containers are healthy at the acceptance endpoint.

## Stage 13 Architecture Review: Reports Center

- Review result: approved. A report is a derived immutable snapshot linked to one completed scan; regenerating creates a new record and never overwrites scan evidence or earlier reports.
- Lifecycle: `queued -> generating -> completed/failed`; failed reports can be retried while preserving their audit history.
- Evidence: each snapshot stores the asset list, knowledge version, severity summary, findings and current disposition state. PDF and Word files are generated locally and carry a SHA-256 digest.
- Access and audit: report APIs require the existing administrator token. Downloads append operator, time and action to the report audit history; no normal delete endpoint is exposed.
- UI: operators can filter by asset, format and status, preview report evidence, generate PDF/Word from completed scans, retry failures and download completed files.
- Stage gate: stop after Reports Center delivery for PM acceptance before starting Server Monitoring Center.
- Delivery verification: Ruff, 58 backend tests, 8 structure tests, 27 frontend tests, TypeScript/Vite build, rendered PDF and Word visual inspection, PostgreSQL migration `20260907_09`, container-local document generation, and Docker end-to-end verification passed.

## Planned Shared Operations Pages

- Server Monitoring Center: SSH agentless read-only collection first, covering availability, CPU/load, memory/Swap, disk/inode, network, uptime, processes, systemd, Docker, trends, abnormal rankings, thresholds, duration, recovery notifications and maintenance windows. A lightweight agent and Prometheus or VictoriaMetrics are later scale paths.
- Web SSH: xterm.js connects only to a backend WebSocket SSH gateway. Credentials are decrypted server-side and never reach the browser. The first stage includes connection lifecycle and session audit; recording, bastions and separately authorized file transfer remain later extensions.
- AI terminal assistant: supports analysis-only, assisted operation and controlled execution. Context is redacted before inference, the model cannot access credentials, and medium/high-risk commands require per-command confirmation or approval.
- Updated order after Reports Center acceptance: Server Monitoring, Web SSH and session audit, AI terminal assistant and confirmed execution, then Security Events and Alerts. Other distribution intelligence feeds and domestic signed-package imports remain deferred.

## Stage 14 Architecture Review: Server Monitoring Center

- Review result: approved. Monitoring is split into per-asset policy, immutable metric snapshots, current alert state, and append-only monitoring events.
- Collection boundary: the backend decrypts an asset credential only for the SSH connection and runs a fixed set of bounded, read-only Linux commands. The browser receives normalized metrics and never receives credentials or raw SSH output.
- Coverage: each snapshot can include availability, CPU/load, memory/Swap, disk/inode, network counters, uptime, top processes, systemd services, and Docker containers. Optional systemd or Docker access failure does not fabricate data or fail core collection.
- Alert lifecycle: CPU, memory, Swap, disk, inode, load, and availability must remain abnormal for the configured duration before triggering. Maintenance windows suppress new events; recovery is recorded separately when enabled.
- Scheduling: credential-bound assets are collected at a server-side interval, defaulting to 300 seconds. The first pass waits one full interval so a backend restart does not immediately fan out SSH connections.
- Scale path: this phase is SSH agentless and sequential. A lightweight agent and Prometheus or VictoriaMetrics remain the planned path for higher collection frequency or larger fleets.
- Stage gate: stop after Server Monitoring Center delivery for PM acceptance before starting Web SSH and session audit.
- Delivery verification: Ruff, 62 backend tests, 8 structure tests, 28 frontend tests, TypeScript/Vite build, migration `20260907_10`, and Docker end-to-end verification passed. All three containers are healthy at the acceptance endpoint.

## Stage 15 Architecture Review: Web SSH and Session Audit

- Review result: approved. HTTP management endpoints retain bearer authentication; WebSocket connections use a random 30-second, single-use ticket sent in the first frame rather than the URL.
- Credential boundary: the browser submits only an asset identifier. The backend decrypts the assigned credential in memory and passes it directly to Paramiko; tickets, APIs, and audits never contain credential material.
- Host identity: Asset Management probes a candidate key for independent administrator verification and exact confirmation. Interactive SSH loads only the database-registered key into temporary Paramiko HostKeys with RejectPolicy; unknown, changed, or stale host/port bindings fail closed.
- Session controls: terminal size and input frames are bounded. Idle timeout, maximum duration, connection cap, operator disconnect, and restart recovery prevent abandoned sessions.
- Audit contract: asset, operator, client address, timestamps, lifecycle status, sanitized failure type, and byte counters are retained. Command contents are intentionally not recorded.
- Scope boundary: AI assistance, command approval, file transfer, bastions, and session recording remain deferred. Stop after this page for PM acceptance.
- Delivery verification: Ruff, 67 backend tests, 8 structure tests, 30 frontend tests, TypeScript/Vite build, migration 20260908_11, and Docker end-to-end verification passed. All three containers are healthy.

## Stage 16 Architecture Review: Data Lifecycle Correction

- Review result: approved. Connectivity and execution status remain unchanged; assets now use a separate `enabled / disabled / archived` lifecycle and terminal scan tasks use an independent archive flag.
- Evidence boundary: normal business operations never physically delete scan, vulnerability, report, monitoring, or terminal audit evidence. Archived records are hidden by default and can be restored.
- Operational boundary: disabled or archived assets cannot enter new scans, monitoring collection or scheduling, connectivity checks, or Web SSH sessions.
- Test cleanup: only `test` environment assets can be physically cleaned after dependency preview, a recorded reason and operator, and exact second confirmation. Shared scans are rejected, while the independent cleanup audit survives deletion.
- UI: Asset Management supports lifecycle views, enable/disable, archive/restore, and guarded test cleanup. Scan Tasks supports current, archived, and all views with archive/restore for terminal tasks.
- Stage gate: stop after lifecycle correction delivery for PM acceptance before starting the AI terminal assistant.
- Delivery verification: Ruff, 71 backend tests, 8 structure tests, 33 frontend tests, TypeScript/Vite build, fresh SQLite migration, PostgreSQL migration `20260908_12`, and Docker end-to-end verification passed. All three containers are healthy.

## Stage 17 Architecture Review: Terminal Assistant and Unified Workspace

- Review result: approved. The existing Dashboard is upgraded in place as the Linux security operations workspace; no duplicate overview page is introduced.
- Dashboard projection: archived assets are excluded, disabled assets are separated, and enabled assets are classified as online, offline, or uncollected from their latest monitoring snapshot. Resource exceptions are ordered first. Vulnerabilities use the latest effective asset and CVE state.
- Monitoring semantics: SSH agentless collection is explicitly near-real-time. The workspace exposes resource trends, active alerts, scan activity, knowledge freshness, and per-server operating and vulnerability posture without claiming second-level telemetry.
- Assistant boundary: terminal output is redacted before local inference and the assistant never receives credentials. Analysis-only returns no executable commands. Assisted suggestions are auditable; controlled commands require exact human confirmation and the backend WebSocket independently rejects unapproved execution.
- Audit boundary: AI suggestions, review decisions, reviewers, timestamps, risk and dispatch state are retained. Ordinary terminal input remains outside command-content auditing. A dispatched command is not treated as verified remediation; the separate validation step remains visible.
- Stage gate: stop after this combined page delivery for PM acceptance before starting Security Events and Alerts. Other distribution feeds and domestic signed-package imports remain deferred.
- Delivery verification: Ruff, 74 backend tests, 8 structure tests, 34 frontend tests, TypeScript/Vite build, fresh SQLite migration, PostgreSQL migration `20260908_13`, and Docker end-to-end verification passed. All three containers are healthy.
- PM consolidation: the standalone Server Monitoring page and navigation entry were removed because its operator view is fully covered by the unified Dashboard. Monitoring collection, policies, snapshots, alerts, history and backend APIs remain active as Dashboard data services.
- PM terminal layout decision: the Remote Terminal page no longer lists or requests session audit history. The SSH terminal and AI assistant now fill the available workspace; only server selection, connect and disconnect controls remain above them. Backend connection-lifecycle records are retained for ticket enforcement, timeout and concurrency controls, but are not exposed on this page.

## Stage 18 Architecture Review: Navigation Framework and Confirmation Interaction

- Review result: approved. Existing pages are grouped under six expandable primary modules: Workspace, Resource Center, Operations Center, Security Center, Events and Audit, and Knowledge and AI. Undeveloped modules are not rendered.
- Navigation behavior: the active page is highlighted, its primary group remains expanded, the desktop sidebar can collapse as a whole, and mobile navigation remains an overlay drawer. Server Monitoring stays consolidated into the Dashboard and is not restored as a standalone entry.
- Confirmation contract: destructive asset cleanup and controlled terminal execution display the required phrase directly. Leading and trailing whitespace is ignored, while case and phrase content remain exact. Frontend enablement and backend validation use the same rule.
- Safety boundary: incorrect phrases remain disabled or are rejected by the backend. AI cannot approve or execute its own command; exact human approval and the independent WebSocket approval check remain mandatory.
- Stage gate: stop after delivery for PM acceptance before starting Security Events and Alerts.
- Delivery verification: Ruff, 74 backend tests, 8 structure tests, 35 frontend tests, TypeScript/Vite build, Docker end-to-end verification, and migration `20260908_13` passed. All three containers are healthy.

## Stage 19 Architecture Review: Security Events and Alerts

- Review result: approved. The page projects monitoring events, failed scans, knowledge synchronization failures and current vulnerability cases into one operational queue without modifying source evidence. Archived assets are excluded.
- Disposition lifecycle: open, acknowledged, in progress and manually closed states are stored as append-only records with operator, reason and time. Source recovery is displayed independently from manual closure.
- Filters: operators can combine asset, severity, source, disposition and date filters. Event identifiers are stable for each source record.
- Monitoring policy: thresholds, sustained duration, recovery notification and maintenance windows use the existing monitoring policy API and are configured from the alerts page after removal of the standalone monitoring page.
- Safety boundary: this page cannot run remediation or terminal commands. Vulnerability findings, scan results and monitoring events remain immutable evidence.
- Stage gate: stop after delivery for PM acceptance before opening another module.
- Delivery verification: Ruff, 77 backend tests, 8 structure tests, 36 frontend tests, TypeScript/Vite build, fresh SQLite migration, PostgreSQL migration `20260909_14`, and Docker end-to-end verification passed. All three containers are healthy.

## Stage 20 Architecture Review: Scheduled Scans

- Review result: approved. Scheduled scans are a separate Security Center page and create new immutable scan tasks through the existing read-only state machine.
- Rules: operators can schedule a daily time in `Asia/Shanghai` or a fixed interval in hours, pause or resume a plan, and inspect linked task history.
- Asset boundary: disabled, archived or missing assets cannot be added to or re-enable a plan. Invalid due plans fail closed and are disabled to avoid repeated execution.
- Evidence boundary: each run retains its asset snapshot, knowledge release and result independently; pausing a plan never deletes historical tasks.
- Stage gate: stop after delivery for PM acceptance before opening another module. Other distribution feeds and domestic signed-package imports remain deferred.
- Delivery verification: Ruff, 81 backend tests, 8 structure tests, 37 frontend tests, TypeScript/Vite build, fresh SQLite migration, PostgreSQL migration `20260909_15`, and Docker end-to-end verification passed. All three containers are healthy.

## Stage 21 Architecture Review: Linux Baseline and Compliance

- Review result: approved. The page runs a versioned built-in Linux security baseline against enabled assets through bounded SSH read-only commands.
- Lifecycle: `queued -> scanning -> aggregating -> completed/failed/cancelled`; terminal tasks can be rerun as new immutable records.
- Evidence: each task retains its asset snapshot, policy version, observed and expected values, severity, result, timestamps and execution log.
- Safety boundary: the page cannot alter configuration or dispatch remediation commands. Disabled and archived assets are rejected.
- Initial coverage: SSH root login, SSH password authentication, host firewall, time synchronization and audit service. Broader CIS profiles remain a later extension.
- Stage gate: stop after delivery for PM acceptance before opening another page.
- Delivery verification: Ruff, 84 backend tests, 8 structure tests, 38 frontend tests, TypeScript/Vite build, fresh SQLite migration, PostgreSQL migration `20260909_16`, and Docker end-to-end verification passed. All three containers are healthy.

## Stage 22 Architecture Review: Server Exposure Surface

- Review result: approved. Enabled assets are inspected through a bounded SSH read-only `ss` snapshot; no external port probing is performed.
- Lifecycle: `queued -> scanning -> aggregating -> completed/failed/cancelled`; reruns create new immutable task evidence.
- Evidence: protocol, bind address, port, process, interface scope, collection source and conclusion are retained with the asset snapshot and methodology version.
- Correctness boundary: binding all interfaces is marked as potential exposure requiring firewall and external reachability confirmation, never as proven Internet exposure.
- Safety boundary: disabled and archived assets are rejected. Weak-password testing and active network probing are explicitly outside this stage.
- Stage gate: stop after delivery for PM acceptance before opening another page.
- Delivery verification: Ruff, 87 backend tests, 8 structure tests, 39 frontend tests, TypeScript/Vite build, fresh SQLite migration, PostgreSQL migration `20260909_17`, and Docker end-to-end verification passed. All three containers are healthy.

## Stage 23 Architecture Review: Managed Password Risk

- Review result: approved. Password credentials already stored by the platform are assessed locally in backend memory against a versioned policy.
- Lifecycle: `queued -> assessing -> aggregating -> completed/failed/cancelled`; reruns create new immutable evidence.
- Evidence: credential name, username, asset count, triggered rule identifiers, risk level, policy version and timestamps are retained. Password values, hashes, lengths and derivable fragments are never persisted or returned.
- Safety boundary: no SSH authentication, password spraying, dictionary attempts or remote account interaction occurs. Private-key credentials are excluded.
- Initial rules: minimum length, character variety, common passwords, username inclusion and repeated characters.
- Stage gate: stop after delivery for PM acceptance before opening another page.
- Delivery verification: Ruff, 90 backend tests, 8 structure tests, 40 frontend tests, TypeScript/Vite build, fresh SQLite migration, PostgreSQL migration `20260909_18`, and Docker end-to-end verification passed. All three containers are healthy.

## Stage 24 Architecture Review: Unified Audit Log

- Review result: approved. Existing append-only audit evidence is projected into one read-only page without introducing a second audit store.
- Sources: event dispositions, asset lifecycle and compliant cleanup, vulnerability dispositions, report operations and AI command approvals.
- Query: source, operator, exact action and date filters with stable pagination and newest-first ordering.
- Safety boundary: credential material, terminal context and ordinary terminal input are excluded. The API requires the administrator token and offers no mutation endpoint.
- Stage gate: stop after delivery for PM acceptance before opening another page.
- Delivery verification: Ruff, 92 backend tests, 8 structure tests, 41 frontend tests, TypeScript/Vite build, PostgreSQL migration `20260909_18`, unified audit API checks and Docker end-to-end verification passed. All three containers are healthy.

## Stage 25 Architecture Review: Patch and Change Center (completed)

- Review result: approved for a staged read-only scope. The page derives patch and remediation advice from immutable vulnerability findings and links each item to its asset, CVE, package, current version, target version, vendor source and evidence chain.
- Safety boundary: every item defaults to `pending_approval` with `execution_enabled=false`. This stage exposes no approval, installation, upgrade, restart, rollback or dispatch endpoint. AI may generate advice but cannot execute it.
- Verification boundary: remediation effectiveness must be established by a subsequent rescan; a target or fixed version alone is not proof of remediation. Original scan evidence remains immutable.
- Data scope: the current endpoint reads the latest 200 findings; aggregation by latest valid asset+CVE state will follow the existing vulnerability lifecycle. Real sources for other distributions and domestic signed package imports remain deferred.
- Stage gate: target tests, full backend/structure verification, frontend target tests, build, migration and Docker checks pass; stop for PM acceptance before starting another page.
- Delivery verification: backend 94 tests, structure 3 tests, frontend target 3 tests, Ruff, TypeScript/Vite build, PostgreSQL migration `20260909_18` and Docker end-to-end verification passed.

## Stage 26 Architecture Review: Patch Approval and Rescan Verification

- Scope: a human operator can approve a patch recommendation with an exact confirmation phrase; approval moves the linked vulnerability case to `pending_verification`.
- Safety boundary: approval records intent only. No package installation, service restart, remote command, automatic dispatch or AI execution is exposed.
- Verification: the existing read-only rescan path remains the only route to `fixed`; the original finding and evidence chain remain immutable.
- UI boundary: the confirmation phrase is visible, whitespace is normalized server-side, and the approve action is disabled until the exact phrase is entered.
- Stage gate: run full backend, frontend, migration and Docker verification before PM acceptance.
- Delivery verification: Ruff, 94 backend tests, 3 structure tests, target frontend tests, TypeScript/Vite build, PostgreSQL migration `20260909_18` and Docker end-to-end verification passed.

## Stage 27 Architecture Review: Patch Advice Aggregation

- Scope: patch advice is deduplicated by `asset_id + cve_id` using the newest finding; historical scan count and latest scan metadata remain visible.
- Correctness boundary: aggregation changes presentation only and never overwrites immutable findings or evidence. Disposition status continues to come from the vulnerability case.
- Stage gate: target tests, full verification, build and Docker checks must pass before PM acceptance.
- Delivery verification: 7 target backend tests, Ruff, TypeScript/Vite build, PostgreSQL migration `20260909_18` and Docker end-to-end verification passed.

## Stage 28 Architecture Review: AI Agent Multi-provider Keys

- Scope: retain the local rules provider and expose disabled external provider entries for OpenAI, DeepSeek and Qwen. External providers require an administrator-configured API key.
- Secret boundary: API keys are encrypted server-side with the existing AES-256-GCM credential vault. The browser receives only an eight-asterisk mask and never receives plaintext.
- Execution boundary: adding a provider key does not enable direct remediation or command execution. AI recommendations still require human review and `can_execute=false`.
- Stage gate: run full backend, frontend, migration and Docker verification before PM acceptance.
- Delivery verification: 5 AI provider backend tests, 2 AI Agent frontend tests, Ruff, TypeScript/Vite build, PostgreSQL migration `20260911_19` and Docker end-to-end verification passed.

## Stage 29 Architecture Review: AI Provider Key Validation and Enablement

- Scope: administrators can validate that a configured provider key decrypts correctly and enable or disable the provider without changing the local default engine.
- Network boundary: validation is local decryption only and explicitly reports `network_called=false`; no vendor endpoint is contacted in this stage.
- Secret boundary: responses contain only masked state and never return plaintext or ciphertext. Disabled providers cannot be selected for triage.
- Stage gate: target tests, full verification, build, migration and Docker checks must pass before PM acceptance.
- Delivery verification: 98 backend tests, 2 AI Agent frontend tests, Ruff, TypeScript/Vite build, PostgreSQL migration `20260911_19` and Docker end-to-end verification passed.

## Stage 30 Architecture Review: AI Provider Configuration Audit (completed)

- Scope: provider key configuration, local decryption validation, enablement and disablement are recorded in the existing unified audit log with operator, provider, action, result and timestamp.
- Secret boundary: audit rows never contain API keys, ciphertext, terminal content, prompts or model responses.
- Operator handling: operator values are normalized for surrounding whitespace before validation and audit persistence.
- Delivery verification: AI Provider API 7 tests, audit log API 2 tests, backend full suite 98 tests, Ruff, TypeScript/Vite build, migration `20260914_20` and Docker end-to-end verification passed. Three containers reported healthy; provider audit responses contain no plaintext key material.
- Stage result: implementation and platform-level verification are complete.

## Stage 31: AI Provider Audit Visibility (completed)

- Architecture review: reuse the administrator-only unified audit endpoint with exact source filtering. No new database table, migration or mutation endpoint is introduced.
- UI: AI Agent opens a read-only configuration audit dialog on demand, with server-side pagination, mobile horizontal scrolling, empty state and retryable errors. Configuration, validation and enablement records do not include key material.
- Scope: preserve local inference and human approval boundaries. Dashboard is already implemented and is not rebuilt. External inference and deferred intelligence sources remain unchanged.
- Verification: AI Agent and unified audit tests passed in the Docker Compose workspace. TypeScript, frontend production build and Docker deployment verification passed; the later platform full suite also passed.

## Stage 32: System Administration Runtime Status (completed)

- Scope: administrator-only read-only runtime status for database connectivity and engine, migration revision, monitoring and knowledge synchronization intervals, terminal limits and SSH host-key verification, plus explicit scan, AI and credential security boundaries.
- Safety boundary: the endpoint never returns database URLs, credentials, master keys, token values, provider keys or mutable configuration controls. Database probe errors degrade to `unavailable` without exposing exception text.
- UI: the System Administration page presents service state, terminal policy and security boundaries with retryable loading; it does not claim server monitoring is second-level real-time.
- Delivery verification: backend target test 1 passed, frontend target tests 2 passed, Ruff, TypeScript, Docker build and deployment verification passed. The system status endpoint reports the active migration without exposing secrets.
- Delivery verification: backend target test 1 passed, frontend target tests 2 passed, Ruff, TypeScript, Docker build and deployment verification passed. Three services remained healthy and migration `20260914_20` stayed active.

## Stage 33: Service Vulnerability Matching Enhancement (completed)

- Scope: extend the existing read-only service inventory path with vendor-advisory matching when service product and version evidence is available.
- Correctness: vendor security advisories remain authoritative; generic version ranges are supporting evidence only. Unknown product mapping, backport ambiguity and incomplete banners remain `manual_confirmation`, never confirmed by version comparison alone.
- Source boundary: no new online feed is enabled in this stage. Other distribution sources and domestic signed packages remain deferred.
- Delivery verification: service matching target tests passed (`8 passed`) with Ruff; full backend validation passed (`101 passed`).

## Stage 34: Automation and Change Workspace (completed)

- Scope: add a unified read-only workspace for existing scheduled scans and patch/change recommendations.
- Execution boundary: the workspace only aggregates queues; scan execution remains read-only and patch actions require explicit human confirmation. No remote command or AI direct execution is exposed.
- Data boundary: schedules and immutable findings are read through existing APIs; no duplicate task or evidence store is introduced.
- Stage gate: targeted frontend test, TypeScript, full validation and Docker checks must pass before PM acceptance.

## Stage 35: Production Multi-user Authentication (completed)

- Scope: browser users authenticate with username and password. Passwords use Argon2id hashes, while opaque server sessions store only SHA-256 token digests and are delivered through `HttpOnly`, `SameSite=Lax` cookies.
- Authorization: administrators manage users and sessions, operators perform daily work, and auditors are read-only. Disabled users lose active sessions; the last active administrator and self-disable paths are protected.
- Audit boundary: successful and failed logins are recorded, and business operators are derived from the authenticated server identity. Browser-supplied operator names are not trusted.
- Production boundary: HTTPS enables secure cookies, HSTS and CSP. `API_ADMIN_TOKEN` remains an audited break-glass and deployment-verification path, not an interactive login method.
- Delivery verification: backend full suite 109 tests, structure 8 tests, authentication frontend 7 tests, Ruff, TypeScript, Vite production build and both Compose configurations passed. Docker password login, current-user lookup, logout revocation, masked secrets and migration `20260915_21` were verified with healthy services.

## Stage 36: Service Catalog and CMDB (completed)

- Review result: approved. The first Service Catalog and CMDB stage is hosted incrementally in the existing application behind a dedicated `/api/v1/cmdb` domain and one secondary page. Existing asset, authentication and unified-audit contracts are reused so the domain can later be extracted as a shared platform service without rewriting data.
- Page scope: browse and maintain business systems, applications, services, APIs and server relationships in one page. The initial experience includes hierarchical search, lifecycle filters, detail editing, and service-to-asset link or unlink actions. Existing assets may remain explicitly unassigned; migration must not invent ownership.
- Persistence: add `cmdb_businesses`, `cmdb_applications`, `cmdb_services`, `cmdb_service_apis`, `cmdb_service_assets` and append-only `cmdb_change_audits`. Use the existing 36-character UUID convention. Cardinality is business 1:N application, application 1:N service, service N:M asset, and service 1:N API.
- Correlation identifiers: entity primary keys are the canonical `business_id`, `application_id`, `service_id` and `api_id`; relationship responses retain the existing `asset_id` and include ancestor IDs. Business codes are globally unique, while application, service and API codes are unique within their parent scope.
- Relationship contract: one durable row per `service_id + asset_id` stores role, state, linked time and unlinked time. Unlinking changes state and emits audit evidence instead of deleting the relationship history. One asset may host multiple services and one service may span multiple assets.
- API recommendation: `GET /cmdb/tree`; collection and item `GET/POST/PATCH` routes for `/cmdb/businesses`, `/cmdb/applications`, `/cmdb/services` and `/cmdb/apis`; `POST /cmdb/services/{service_id}/assets`; and `PATCH /cmdb/service-assets/{relation_id}`. No ordinary physical-delete endpoint is included.
- Authorization: administrators and operators may maintain catalog records and relationships; auditors remain read-only. Archive, restore and any future compliance cleanup are administrator-only. Operators are derived from authenticated server sessions. `owner_user_id` represents accountability only; row-level authorization and multi-tenancy are deferred.
- Audit: create, update, archive, restore, link and unlink actions append entity type, entity ID, action, before/after snapshots, operator user ID, username snapshot, time and `trace_id` to `cmdb_change_audits`. The existing unified audit endpoint projects these records rather than creating a second audit system.
- Retention: master records use `active`, `disabled` and `archived` lifecycle states. Archived records, relationship history and audit evidence are retained by default, with no automatic purge before a platform retention policy is approved. CMDB records never store SSH credentials, provider keys or other secrets.
- Migration: create one Alembic revision after `20260915_21` containing additive tables, foreign keys and indexes only. Existing assets, scans, findings, reports and terminal evidence are neither altered nor backfilled. Existing APIs remain compatible, and SQLite plus PostgreSQL migration tests and a production backup are required before Docker acceptance.
- Deferred boundaries: dependency topology, teams and organizations, configuration-item versioning, cloud discovery, telemetry bindings and automatic discovery remain later work. Alerts, incidents, tickets, tasks, changes and deployments will add nullable correlation IDs and immutable display snapshots when their domain stages are implemented.
- Stage gate: after UI review, implement only the Service Catalog and CMDB page and its backend contracts, migration and tests. Permission, audit, compatibility and Docker verification must pass before PM acceptance or work on another platform center begins.
- Delivery verification: backend full suite 117 tests, structure 8 tests, frontend full suite 56 tests, Ruff, TypeScript and the Vite production build passed. A pre-migration PostgreSQL backup and SHA-256 digest were created; migration `20260915_22`, all three healthy containers, the business-to-API hierarchy, durable service-asset relationships, unified audit projection and sensitive-data checks passed Docker acceptance.

## Stage 37: Unified Workspace and CMDB Impact View (completed)

- Architecture review: approved. Extend the existing Dashboard as a read-only operational projection of current CMDB context; do not add a standalone monitoring page, duplicate CMDB data, or a migration.
- API scope: extend `GET /api/v1/dashboard` with current asset-to-business, application and service summaries using `asset_id`; preserve `business_id`, `application_id` and `service_id` for future alert, incident and ticket correlation.
- Data rules: archived ancestors and unlinked service-asset relations are excluded from current impact views. Assets without an active relation are explicitly labeled `未编目`; immutable vulnerability, scan and monitoring evidence is unchanged.
- UI scope: add service context to the Dashboard server table and a business impact panel aggregating resource state, active alerts, pending vulnerabilities and hosted services. Navigation only opens the existing CMDB or vulnerability pages.
- Security boundary: reuse the authenticated read-only API, expose no credentials, keys or audit internals, and keep SSH agentless collection labeled as near-real-time.
- Stage gate: target tests first, then Ruff, TypeScript, production build, full suite and Docker Compose verification before PM acceptance; no next platform-center page before this gate.
- Delivery verification: backend 118 tests and Ruff passed; Dashboard and CMDB frontend tests passed (CMDB rerun with a 15-second target timeout due to the existing 5-second first-test budget), TypeScript/Vite production build passed, and Docker Compose rebuild plus deployment script completed with all three containers healthy and `/api/v1/dashboard` returning 200.

## Stage 38: Service Observability Foundation (completed)

- Architecture review: approved. Add one service-centric read-only page and `/api/v1/observability/services`; reuse current CMDB relationships, SSH agentless snapshots, active alerts and current vulnerability state without restoring the retired standalone server-monitoring page.
- Status contract: healthy means all observable enabled assets are online with no active resource alert; degraded means partial availability or active alerts; unavailable means all observable enabled assets are offline; unobserved means no current usable sample or only disabled assets.
- Data boundary: only active CMDB ancestors and relations and non-archived assets participate. Preserve `business_id`, `application_id`, `service_id` and `asset_id`; do not mutate or duplicate monitoring, alert, scan or vulnerability evidence.
- UI scope: operational metrics plus search, business and status filters, followed by an exception-first table showing hosted assets, resource peaks, alerts and severe/high vulnerability counts. Data remains explicitly near-real-time, not second-level real-time.
- Security and delivery: authenticated read-only access for all existing roles, no secret or execution surface, no migration; target tests precede one full test/build/Docker gate.
- Delivery verification: backend full suite 120 tests and Ruff passed; frontend full suite 57 tests, TypeScript and Vite production build passed; Docker Compose rebuild completed with PostgreSQL, backend and frontend healthy, migration `20260915_22` retained, and the deployment verification chain passed.

## Stage 39: Login and Main Workspace Visual Refresh (completed)

- Scope: preserve username/password authentication, HttpOnly session cookies, role permissions and navigation behavior while refreshing the login screen and primary workspace chrome.
- UI: responsive two-panel login layout with explicit security posture and internal deployment status; branded AppShell with page context, search affordance and compact user role metadata; mobile drawer navigation remains available.
- Security: no access-token input, credential rendering or authentication protocol changes.
- Delivery verification: AccessGate and AppShell target tests (5) passed, TypeScript and Vite production build passed, Docker Compose rebuild completed with all three services healthy, and deployment verification remained green at `http://localhost:8088`.

## Stage 40: Login Companion States (completed)

- Scope: update only the login page inside the existing two-panel layout; AppShell and the main workspace remain unchanged for the next gated phase.
- Interaction: the CSS-only companion supports idle, input, closed-eyes, loading, failure, success, welcome and wave states. Focus, submit/loading, successful authentication and failed authentication drive the state machine; hover triggers the wave state.
- Animation boundary: no animation library is introduced. Motion uses CSS `transform` and `opacity` transitions/animations and remains responsive on mobile.
- Security: username/password authentication, HttpOnly session cookies and existing error handling are unchanged; no token, password or key is rendered.
- Delivery verification: AccessGate target tests (3) passed, TypeScript and Vite production build passed, Docker Compose rebuild completed with all three services healthy, and deployment verification remained green at `http://localhost:8088`.

## Stage 41: Reference-Inspired Login Scene (completed)

- Reference boundary: borrow the supplied CareerCompass page's split composition, large geometric character scene and restrained form rhythm without copying its branding, imagery or source.
- UI: replace the single login mascot with a multi-character geometric scene while retaining the eight login state labels and state-driven feedback. CSS shapes and `transform`/`opacity` motion are used without an animation dependency.
- Security: authentication endpoints, HttpOnly session cookies, username/password flow and sensitive-data boundaries remain unchanged.
- Delivery verification: AccessGate target tests (3) passed, TypeScript and Vite production build passed, Docker Compose rebuild completed with all three services healthy, and deployment verification remained green at `http://localhost:8088`.

## Stage 42: Fullscreen Dynamic Login Scene (completed)

- Scope: keep the existing username/password authentication contract and update only the login page presentation.
- UI: remove the centered card treatment; the split login layout now fills the browser viewport, with full-height intro and form regions and responsive mobile stacking.
- Motion: add continuous grid drift, staggered character breathing/drift, orbit and pulse atmosphere, plus reduced-motion support. Existing eight state labels and state-driven transitions remain intact.
- Security: no changes to authentication endpoints, HttpOnly cookies, password handling or sensitive-data rendering.
- Delivery verification: frontend production build passed in the Docker Compose build, and the rebuilt frontend served HTTP 200 at `http://localhost:8088`; Vitest and local TypeScript commands were not used as a passing signal in that verification run.
