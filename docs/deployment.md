# Docker Deployment

## 告警接入运行状态

“监控告警 → 活动告警 → 接入连接器”增加管理员运行摘要及接收记录。成功次数包含新建、
重复和复发告警；失败记录仅保留固定错误码、接收时间和状态，不保存原始载荷、签名或密钥。
“最近接收成功”仅证明该请求已入库，不代表 SSH 诊断、管理员通知或故障恢复已经完成；
尚无请求不推断健康，也不回填旧接入记录。停用连接器显示“已停用”。

接口为 `GET /api/v1/alert-connectors/runtime` 和
`GET /api/v1/alert-connectors/{id}/receipts?offset=0&limit=20&result=failure`，
均仅系统管理员可读。分页每次最多100条；接收记录保持追加留存，无普通业务删除入口。
生产发布本阶段前必须执行 Alembic `20261031_45 -> 20261106_46`，不需修改 `.env` 或 Compose。
接收观察写入失败会记录固定服务日志，不能使验签失败请求获准；若告警证据已提交但详情查询
暂不可用，Webhook仍返回成功状态及 `accepted=true, detail_available=false` 的最小确认，
避免发送端误重试和一次接收被重复统计。

## 使用文档与只读诊断接入

登录后点击侧栏左下角“使用文档”，在独立浏览器标签页按工作台查看每个功能的作用、准备条件、使用步骤、
适用命令和限制。只读诊断文档位于“监控告警 → 活动告警 → 只读诊断授权”，可查看
SSH、Docker、K8s、MySQL、Redis、RabbitMQ 的准备步骤、示例和执行边界。实际配置入口为：
“监控告警 → 活动告警 → 只读诊断授权”。仅平台管理员可新增、验证和撤销授权；
目标环境的账号、强制网关、代理或 RBAC 必须另由目标管理员准备。
文档使用同源 `?view=docs` 入口并沿用登录及会话校验；“打开相关功能”另开对应功能标签，
文档页保留，顶部“返回平台”可回到主界面。

Docker 接入需要独立受限 HTTPS 代理，当前项目没有代理安装器。
代理必须验证专用 Bearer Token 和 TLS，仅向平台开放：

```text
GET /v1.45/containers/json?all=true&limit=50
GET /v1.45/containers/CONTAINER_ID/stats?stream=false&one-shot=true
```

资源采集仅限本次列表发现的运行容器，完整 ID 由采集器选取。
代理需拒绝所有其他路径、参数和方法，尤其 inspect、exec、日志、重启、删除和文件接口；
不得将无认证 2375、docker 组权限或只读挂载 docker.sock 视作只读授权。

创建已启用、未绑定人工 SSH 凭据的代理映射资产，使用与资产 IP 相同的 HTTPS
地址、有效 IP SAN 证书、CA PEM、独立代理身份和 Token，选择容器状态与资源快照范围。
同一资产只支持一种诊断授权，不能直接转换类型；授权期限最长 90 天。
Linux 和 Kubernetes 的具体准备方式见下文既有章节，平台帮助也提供中文步骤。

保存授权后，人工输入资产名与原因执行“验证只读授权”；该操作实际采集所选只读证据并写入审计，
不是单纯端口探测，也不能证明权限最小化、服务健康或故障恢复。
外部告警诊断结果进入管理员通知流程前还需配置 SMTP 和收件人，并检查实际投递状态。
帮助中的配置命令仅供管理员手动执行；AI 不执行授权配置，人工确认也不将只读诊断升级为写权限。

MySQL 只允许固定状态、变量和复制状态读取；Redis 只允许固定 INFO、客户端和慢日志读取；
RabbitMQ 只允许管理 HTTPS 的 overview、nodes、queues、consumers GET。三类服务都必须使用
专用非管理员账号、TLS/CA 和限定范围，平台不接受任意 SQL、Redis 命令或 RabbitMQ 写操作。

## K8s Read-Only Connector

The K8s connector uses the existing alert page's diagnostic-authorization tab, not SSH or a new menu, and is published in production schema37 at `http://192.168.10.17:8088/`. The isolated candidate at `http://192.168.10.17:8095/` was synthetic DEMO evidence only. An actual administrator session selects an enabled cluster-mapping asset with no SSH credential, supplies the API Server's literal-IP HTTPS origin (IP must equal the asset), dedicated ServiceAccount identity, CA PEM, expiring token, 1-3 explicit namespaces and the allowed observations. DNS endpoints, skipped TLS verification, admin kubeconfig and fallback SSH are unavailable. The API certificate must contain the endpoint's IP SAN.

Install least-privilege RBAC manually on the target; the platform never applies manifests or creates tokens. This example assumes `ops` and `production` already exist. Repeat the Role/RoleBinding only for each explicitly selected namespace. Do not grant cluster-wide permissions, Secrets, ConfigMaps, logs, exec, proxy, impersonation or writes. A Kubernetes resource read can expose raw object fields to the identity; platform evidence uses reviewed status projections and never stores or forwards raw specs/environment/messages.

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: diagnostic
  namespace: ops
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: platform-diagnostic
  namespace: production
rules:
  - apiGroups: [""]
    resources: ["pods", "events"]
    verbs: ["list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "daemonsets"]
    verbs: ["list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: platform-diagnostic
  namespace: production
subjects:
  - kind: ServiceAccount
    name: diagnostic
    namespace: ops
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: platform-diagnostic
```

Use an API-audience token whose actual expiry covers the configured authorization; platform validity cannot exceed 90 days and should normally be much shorter. Local JWT subject/expiry checks do not validate its signature or prove least privilege. Independently verify denied actions and narrow the Role if an observation is not enabled. No `watch` permission is needed. GET responses return only masks and CA fingerprint; blank credentials retain the same identity, while target/identity/CA changes require a different token. Configuration and read outcomes remain audited.

Collection is capped at 40 seconds total/5 seconds per request, 3 pages per resource, 50 items per page, 150 total items, 256 KiB per response and 1 MiB total response bytes. Evidence has its own size bound. Authorization, cancellation and lease checks run across pages and reads; incomplete data is partial/failed, not a clean-health claim. Nodes, container logs, metrics, Service/Endpoints and additional connectors remain deferred.

Migration37 adds kind/configuration columns over36 and defaults existing rows to Linux. A downgrade is verified before K8s configuration exists; afterwards it is deliberately refused to preserve transport metadata. Use a reviewed forward fix instead of dropping authorizations/evidence. The separate 8095 candidate uses synthetic assets, a local TLS fixture and no actual cluster, external model or SMTP channel. Production release needs explicit PM approval and must preserve all unrelated server configuration.

## Current Recovery Verification Contract

The October 14-15 recovery release is published at `http://192.168.10.17:8088/`, revision `20261014_36`, on the existing Activities/Alerts page. Publication used 34 approved source/test/migration files and four verified build files; only the original backend container was restarted. Five configuration files, unrelated source, old static assets, encrypted credentials and protected records across 22 tables were retained. Current production accounts remain valid. No DEMO records, recovery jobs, login sessions, target collection or SMTP sends were created by publication checks.

The separate `192.168.10.17:8094` preview remains an isolated synthetic environment, not production. Source tests passed BE559/FE398 before publication; release-tool targets passed 11+2, with real production migration, read-only API/authentication checks, static HTTP hashes, protected-data signatures and independent QA verified. Production currently has no alerts, so positive recovery-history checks used the isolated three-role environment; real authorization, SMTP delivery and browser visual acceptance are still separate requirements.

Migration `20261010_32` supplies recovery tables and notification linkage; existing empty merges `20261012_33`, `20261013_34` and `20261014_36` merge recovery with current production. Upgrade from 35 to 36 adds only recovery DDL. A complete recovery-only rollback first downgrades to `20261014_35`, then explicitly to `20261010_32@20261009_31`; verify a unique 35 head, recovery tables/notification column removed, and all published queue/AI/scan-removal evidence retained. Never downgrade to a common ancestor. This table-dropping rollback is only for a release with no recovery evidence; once records exist, preserve them and use a reviewed forward fix. The release script's EXIT handler only restarts the original backend; it is not an automatic source or database rollback.

Release admission blocks scanning/aggregating scans, waiting/connected terminals, and queued/running/retry-wait diagnostic jobs. Historical queued scans require an explicit human start and have no startup consumer; all 132 existing queued scans were preserved with the other protected scan records, not cancelled or executed. Diagnostic admission `revision` is a polling semaphore and may advance; its singleton identity/count and all job/attempt/slot/grant evidence remain protected.

GET `/api/v1/alerts/{alert_id}/recovery-verifications` lists history, available checks and the cross-page active-verification flag. POST creates a verification; POST `/{verification_id}/evaluate` or `/cancel` requires an actual admin/operator session, exact alert-ID confirmation (outer whitespace trimmed), reason of 3-1000 characters and, for existing records, `expected_version`. UI confirmations are `发起恢复验证`, `核验恢复证据`, and `取消恢复验证`; they map to the existing alert-ID contract. Auditors and break-glass bearer tokens cannot perform these human actions.

告警复发后，上一轮恢复记录继续保留，但不会作为本轮恢复结论。未完成的观察任务与复发告警在同一事务内封存，保留评估、时间线和通知记录，并释放活动占位；通知发送失败继续遵守既有重试规则。历史接口的 `is_current=false` 表示历史轮次，只能查看，旧轮核验或取消请求返回 409。新记录通过已有时间线编号绑定轮次，无数据库迁移；旧记录采用保守时间边界，时间相同不作为本轮恢复证明。告警状态更新、复发及恢复核验使用同一告警行锁，前端在收到复发时间变化后清空旧确认内容并重新加载。

The alert must be manually marked in progress/resolved/closed and its asset enabled. Default observation is 15 minutes with a maximum 360-second sampling gap; at least three unique samples strictly after the start must cover the whole window. Missing, stale, future, invalid or changed-target evidence cannot establish recovery. Active recovery blocks changing the asset address/SSH port; cancel first. Only stored monitoring/probe records are inspected: no SSH/HTTP collection or model commands are initiated. Results are observing/verified/failed/blocked/cancelled; immutable evaluations and audit timeline are retained, and terminal notification outbox entries commit atomically. A passed check does not prove whole-service recovery or close the alert. SMTP/recipient configuration and actual delivery remain separate acceptance requirements.

## Current Diagnostic Queue Contract

Current development and acceptance run only in the isolated Docker Compose stack on the Linux deployment host `192.168.10.17`. Development does not authorize publication or changes to production `.env`, Compose, Nginx, credentials or historical records.

The backend defaults to two diagnostic workers, using database-shared slots and a 500-active-job admission limit. Migration `20261009_31` seeds exactly one admission row and two slots. Apply it before starting the candidate backend. `DIAGNOSTIC_WORKERS_ENABLED=false` is reserved for isolated tests/synthetic UI previews; normal operation defaults to true and needs no production Compose change.

The October 14 queue-only release targets `20261014_35`, merging the original queue migration with published `20261013_32`. Ship only the current production migration tree plus `20261009_31` and `20261014_35`; exclude recovery migrations and workspace merges 33/34/36. Published at `http://192.168.10.17:8088/` with AI configuration, query optimization, scan-removal audit and safety-check tabs retained. The 48-file manifest, database migration, read-only API checks, static HTTP assets, original container identities and protected business data passed publication verification. No real target collection or SMTP delivery was triggered by release checks.

For queue-only rollback, first downgrade to `20261013_32`, then explicitly downgrade `20261009_31@20261007_30`. The first step alone leaves the queue branch installed. Verify the only remaining head is `20261013_32`, the queue tables are absent, and AI ciphertext/audits and prior evidence remain. Never downgrade directly to the common revision 30: it would also undo published AI and scan-removal changes.

Manual enqueue: POST `/api/v1/alerts/{alert_id}/diagnostics`, HTTP202. Cancel: POST `/api/v1/alerts/{alert_id}/diagnostic-jobs/{job_id}/cancel`, HTTP202. Both require a real admin/operator session and JSON `confirmation` equal to the alert UUID (outer whitespace trimmed), plus `reason` of 3-1000 characters. Active-job conflict returns409; manual capacity overflow returns429. Cancellation of a running task first projects `cancelling`, not completed cancellation.

The UI requires the exact Chinese phrase `提交只读诊断` or `取消只读诊断` and trims outer whitespace before enabling submission. It maps that human confirmation to the existing alert-ID API contract; it never substitutes AI approval. Closing, reopening or changing the target clears the confirmation draft.

History: GET `/api/v1/alerts/{alert_id}/diagnostic-jobs?offset=0&limit=20`; attempts: GET `/api/v1/alerts/{alert_id}/analyses?job_id={job_id}&offset=0&limit=50`. Limits are capped at100. Job tokens and leases are never returned to the browser. Ninety-second expired claims seal only their own running attempt and enqueue its result once; retryable failures use 10/30-second backoff, three total attempts. Partial, authorization, key, host-policy and malformed-protocol outcomes do not automatically rerun. Notifications still need independently configured SMTP/recipients; an outbox record does not mean delivery.

The supported development, build, test, and deployment-test environment is a Linux host with Docker Engine and Docker Compose. The same Compose files are the deployment contract for every environment.

## Services

- `frontend`: Nginx serves the React application and proxies `/api` to the backend.
- `backend`: FastAPI runs Alembic migrations before accepting traffic.
- `postgres`: PostgreSQL stores application data in the `postgres_data` named volume.

Only the frontend port is published. PostgreSQL and FastAPI remain on the internal Compose network.

## First Start

Create the local environment file and replace every secret placeholder:

```bash
cp deploy/.env.example deploy/.env
openssl rand -hex 32
openssl rand -base64 32
openssl rand -hex 32
```

Use the first value as `POSTGRES_PASSWORD`, the base64 value as `CREDENTIAL_MASTER_KEY`, and the final value as `API_ADMIN_TOKEN`. Then start the stack:

```bash
docker compose --env-file deploy/.env up -d --build
docker compose --env-file deploy/.env ps
```

Open `http://localhost:8080` unless `APP_PORT` was changed.
Run the repeatable Docker Compose deployment verification after the containers become healthy:

```bash
bash scripts/verify_deployment.sh
```

## Production Authentication

Normal console access uses usernames and passwords. Passwords are stored only as Argon2id hashes, while the browser receives an opaque `HttpOnly` session cookie. Set `AUTH_BOOTSTRAP_USERNAME` and `AUTH_BOOTSTRAP_PASSWORD` only for the first administrator; once a user exists, startup never recreates or overwrites accounts. The first administrator must change the temporary password after login.

`API_ADMIN_TOKEN` remains a separate high-entropy break-glass secret for deployment verification and emergency recovery. Do not enter it in the browser or share it with normal users.

For an Internet or production-network deployment, terminate TLS before Nginx, set `AUTH_COOKIE_SECURE=true`, and set `ALLOWED_ORIGINS` to the exact HTTPS console origin. Plain HTTP is limited to local or isolated Compose acceptance. The login endpoint is rate-limited by Nginx and repeated password failures lock the account temporarily.

The included production override terminates TLS inside the frontend container. Set `ALLOWED_ORIGINS=https://sentinel.example.com`, `TLS_CERT_PATH`, and `TLS_KEY_PATH` to protected host files, then run:

```bash
docker compose --env-file deploy/.env \
  -f compose.yaml -f compose.production.yaml up -d --build
```

Port 80 remains available only for redirecting to HTTPS. Certificate renewal stays with the host or external certificate manager; reload the frontend container after renewal. A load balancer or ingress may terminate TLS instead, but it must preserve the exact origin and forwarded protocol.

Roles are intentionally small: administrators manage users, operators perform daily security operations, and auditors have read-only access. Disabling a user, resetting a password, or explicitly revoking sessions invalidates active sessions. Login successes and failures appear in the unified audit log without storing passwords or session tokens.

### Login Security Policy

The administrator-only Login Security Policy panel configures consecutive
login failures (3-20), lock duration (60-86400 seconds), new-session lifetime
(900-604800 seconds), and mandatory MFA. The settings are stored together in
`platform_settings.auth_policy`; no migration beyond schema `20261031_45`
is required. Until the first save, the existing server defaults remain active.
Changing the lifetime affects new logins; existing sessions keep their expiry
and can be revoked through the session controls.

Administrators configure MFA under System Management > Security Settings:
optional, administrators only, or all users. Users bind their own authenticator
under the same Security Settings page by scanning a QR code and confirming a
six-digit code. All signed-in users can manage their own MFA; only administrators
can view or change the global login policy and AI settings.
Unbound users subject to enforcement must complete the QR enrollment flow before
logging in. Enrollment expires after ten minutes, is single use, and becomes
invalid after password, account permission or pending-secret changes. No login
session is issued before enrollment is completed. Invalid MFA codes count toward
account lockout; an initial missing-code challenge does not.
With mandatory MFA enabled, resetting the last enabled, MFA-bound
administrator is rejected. Disable the mandatory policy first or bind another
administrator. Successful MFA resets revoke that user's browser sessions and
terminate their SSH connections; the reset and its audit commit together.

A malformed saved policy rejects new logins with HTTP 503 rather than falling
back to weaker defaults. An authenticated administrator or the protected
emergency credential can submit a validated replacement through
`PUT /api/v1/system/auth-policy`; the repair records the unavailable prior
state without copying malformed content into the audit. The authentication
audit API accepts exact `client_ip`, `failure_reason`, and `login_success`
filters; surrounding whitespace is trimmed for the text filters.

Administrator session controls are available under System Management, User
Permissions. `GET /api/v1/auth/sessions/page?user_id=<user-id>&offset=0&limit=20`
returns `items`, `total`, `offset` and `limit` for active browser sessions,
including login, last activity and expiry times, source IP and User-Agent.
The page limit is 1-100; the UI uses 20. Disabled users and expired or revoked
sessions are excluded before counting and paging. Ordering is last activity,
creation time and session ID, all descending. Concurrent changes can still
move rows between pages; the list is not a cross-request snapshot.
The legacy `GET /api/v1/auth/sessions?user_id=<user-id>` retains its unpaged
array response. Neither endpoint returns cookie values or token hashes.
Operators and auditors receive 403. Page-query database failures return 503
without exposing database details; the UI clears stale data and confirmation
state until a successful refresh. If the selected page becomes invalid, the
UI fetches the new last page once, then requires manual refresh if it changes
again.

`POST /api/v1/auth/users/<user-id>/sessions/<session-id>/revoke` revokes exactly
one active session owned by that user and records `session_revoked` in the
access audit. A mismatched, expired or already revoked session returns 404.
The next authenticated request using a revoked cookie is rejected. Revoking
the current browser session returns the UI to login. This does not terminate
existing SSH connections, which are not linked to individual browser sessions.
The separate Revoke All Sessions confirmation calls the existing
`POST /api/v1/auth/users/<user-id>/revoke-sessions` endpoint and also terminates
that user's SSH connections. Refresh failures invalidate old selections;
uncertain writes require a manual refresh rather than automatic retries.

## Knowledge Sources

Offline packages are verified with Ed25519. Configure trusted public keys as a JSON map; private signing keys stay with the package publisher and must never be deployed:

```dotenv
KNOWLEDGE_TRUSTED_KEYS={"vendor-2026":"base64-raw-ed25519-public-key"}
```

CNVD, CNNVD, Kylin, and UnionTech sources default to signed offline-package import. Optional online vendor connectors require HTTPS package and signature URLs and reuse the same signature checks:

```dotenv
KNOWLEDGE_SOURCE_CONNECTORS={"openEuler Security Advisories":{"package_url":"https://vendor.example/advisories.json","signature_url":"https://vendor.example/advisories.json.sig","key_id":"vendor-2026"}}
KNOWLEDGE_SYNC_INTERVAL_SECONDS=14400
```

The built-in openEuler adapter uses the allowlisted official HTTPS security-notice API and validates CVE identifiers, product releases, and fixed RPM versions before publishing. It checks at 02:00, 06:00, 10:00, 14:00, 18:00, and 22:00 Asia/Shanghai; the first successful run replaces demo openEuler data and later runs merge by advisory identifier. Other configured connectors use the same interval. CNVD, CNNVD, and sources without stable APIs remain signed offline-package sources. Downloads, validation failures, imports, version activation, rollbacks, coverage, and freshness alerts are visible in the Knowledge Base page.

Historical openEuler backfill is started manually from the Knowledge Base page. It publishes validated pages incrementally, permits one active job, and resumes from the failed or cancelled page. A backend restart marks an interrupted job failed so an operator can resume it; scheduled incremental synchronization waits while a backfill is active. Do not start a full historical job during a routine deployment check.

## Report Generation

The backend generates PDF and Word reports locally from completed scan snapshots. No report data is sent outside the deployment. Generated binaries, SHA-256 digests, knowledge versions, asset snapshots, vulnerability disposition snapshots, and download audits are stored in PostgreSQL and therefore move with the normal database backup. The backend image includes a CJK TrueType font so Chinese PDF output is deterministic on Linux.

Reports are immutable business records. Regeneration creates another report, failed records can be retried, and the normal API exposes no physical deletion operation. Monitor database growth because report binaries are stored in PostgreSQL; object storage is the planned scale path for large installations.

## Server Monitoring

Server monitoring uses the credential already assigned to an asset and decrypts it only inside the backend. Collection is agentless and read-only: no sudo, remote writes, package installation, service changes, or remediation commands are permitted. Raw SSH output and credentials are never returned to the browser.

Set `MONITORING_INTERVAL_SECONDS` to control automatic collection; the default is 300 seconds and the supported range is 60 to 86400 seconds. The scheduler waits one interval after startup, then collects credential-bound assets sequentially to avoid an SSH connection burst. Manual collection remains available from the monitoring page.

Monitoring snapshots are immutable and retained in PostgreSQL. Per-asset thresholds support a sustained duration, recovery notification, and maintenance window. Plan retention or downsampling before increasing frequency or fleet size; Prometheus or VictoriaMetrics is the intended scale path.

## Service/API Probes

Manual probes use CMDB services and optionally a GET/HEAD API. Set `SENTINEL_PROBE_ALLOWED_CIDRS` to a comma-separated list of explicitly approved destination networks, preferably individual `/32` or `/128` addresses. The default empty value denies target creation and execution. Approve only health endpoints, not platform, container, database or management interfaces.

This first release accepts HTTP/HTTPS IP-literal URLs only. Domains, URL credentials, query strings and fragments are rejected. Loopback, link-local/cloud metadata, multicast and unspecified addresses remain forbidden even if included in the allowlist. HEAD is the default; GET must target an endpoint confirmed to have no side effects. TLS validation stays enabled; self-signed or hostname-only certificates will fail when accessed by IP. Redirects are recorded without following them, environment proxies are ignored, and response bodies are not retained.

Only authenticated administrators/operators can create targets or run checks. Auditors can read targets and append-only result history. Each run validates active CMDB ancestors and stores the target, expected/actual HTTP status, duration, operator and correlation IDs. Passing a single endpoint check does not establish full service health. Scheduling, alert integration and domain-name probing are not part of this stage.


## Web SSH

Web SSH uses xterm.js and the backend WebSocket gateway. The browser requests a short-lived single-use ticket and never receives the SSH credential. Terminal idle timeout, maximum duration, and concurrent connection limits are server-side settings.

An enabled asset with a valid inline SSH password or private key may request a ticket. The first connection accepts the server host key automatically in the current Paramiko client only; it is never returned to the browser, written to `known_hosts`, or persisted as an enrollment record. Asset creation, terminal access, monitoring, vulnerability scans, and inspections all use the same direct connector. Only real authentication, network, port, timeout, private-key-format, or credential-decryption failures block the operation.

Asset write operations require an administrator or an operator with full-asset scope. An assigned operator can edit only explicitly granted assets. The ticket handshake, shell opening, and every interactive input recheck the current account, session, asset lifecycle, and SSH grant; a disabled account, revoked browser session, removed grant, or disabled/archived asset closes the shell before further input.

Manual scan, baseline, and exposure tasks carry the requesting user identity into the background worker. The worker rechecks that identity and its current asset grants before each collection and before publishing evidence. Trusted scheduler jobs use a separate system identity and are not tied to a browser session. These checks are read-only authorization gates and do not grant AI command execution.

Production deployments must use HTTPS/WSS and configure ALLOWED_ORIGINS with the exact application origin. Session audits retain metadata and byte counts but intentionally do not record ordinary command content. File transfer, bastions, and session recording remain outside the current phase.

The terminal AI assistant is strictly analysis-only. Context is redacted before inference and credentials are never included. Legacy approvals, previously approved commands and WebSocket AI dispatch are rejected; human confirmation cannot grant AI write permissions. Human SSH remains separate. Terminal and assistant panels occupy the available workspace.

The November 3 SSH lifecycle change was published on November 4, 2026. It seals the
first terminal outcome with a separate, idempotent database transaction before
awaiting cleanup. A cancelled connection worker retains ownership until its late
shell is closed; cleanup is dispatched before cancellation checkpoints. Existing
terminal evidence cannot be reconnected or overwritten. Final events carry the
stored reason, message and end time; a database outage emits an error instead of
claiming that closure was saved. HTTP202 means disconnection was requested; the
browser waits for the terminal event, then refreshes history and ignores late
frames. Existing authorization and AI read-only boundaries remain. Historical
host-key registration tables and endpoints are compatibility data only and are
not consulted by the direct connector.

Only isolated Docker on `192.168.10.17` was used: 57 distinct backend tests passed,
the two fixture-adjusted files passed an 18-test recheck, and 27 frontend tests,
Ruff and one TypeScript/Vite build passed. During development, production source,
configuration, migrations, static files and container identities/start times
were unchanged.
No migration or dependency installation is needed. The verified artifact is
`/var/tmp/terminal-lifecycle-verified-20261103.tgz`, SHA-256
`df3fdc3d28db47a4eccd433b6e3e5eb6a83fbe766808280d9699dfe4ac5ae187`;
its seven-file runtime manifest excludes unrelated workspace changes.
PM explicitly authorized the November 4 publication. Only the seven runtime
files and four verified static artifacts were installed; only the existing
backend was stopped/started. Source and HTTP static hashes, authenticated
terminal history 200, anonymous 401, lifecycle imports and health checks passed.
Thirty-five protected-table checksums matched before admission, after stopping
the backend and after publication. Configuration, migrations, dependencies,
unrelated source, old hashed assets and other containers were preserved.
Temporary rollback copies were removed after success. Release evidence is
`/var/tmp/linux-ops-terminal-release-20261104.2jsT29`; its downloaded archive
SHA-256 is
`82d8ca2fcfaea3c5b996c1f4f18d0b0d115d28abf03d33a68116cab4fd4c0f46`.
Independent QA verified the paired evidence; the server connection is closed.
Real network interruption has
not had field acceptance; process termination, a database outage or permanently
blocked cleanup cannot guarantee immediate finalization. Existing startup
recovery remains in place.

## Data Lifecycle

Asset connectivity status is separate from the operational lifecycle. Disabled and archived assets are excluded from scanning, monitoring scheduling, and Web SSH; archived assets are hidden from default lists. Completed, failed, and cancelled scan tasks can be archived without altering their evidence or reports. A queued manual task that loses its requester authorization is marked failed with an authorization reason and does not publish partial evidence.

Normal API deletion is intentionally rejected. Physical cleanup is restricted to `test` environment assets through dependency preview and exact second confirmation; shared scan history cannot be cleaned through this path. Every successful cleanup writes an independent audit record that is not linked by a cascading foreign key.

## Operations

### Administrator SMTP Notifications

Configure the channel in the existing alert page, Administrator Notifications tab. Use a trusted SMTP hostname, SSL/TLS or STARTTLS port, sender and up to 20 administrator recipients. Certificates and hostnames are verified; plaintext SMTP is unsupported. The optional authentication password is encrypted in the database using the server credential vault, omitted passwords retain the stored value, and an empty password cannot overwrite it. Keep the vault key outside the database and browser.

The backend lifecycle starts the durable outbox worker. Missing/disabled channels retain blocked work, enabling the channel unblocks retained messages, and failures retry with bounded backoff up to five attempts. After exhaustion, inspect the safe error code and handle escalation manually; retry never resets the budget. Sending is at least once with a stable Message-ID, so duplicate emails are possible after interrupted success or partial recipient refusal. SMTP acceptance does not mean the administrator read the mail. Human view acknowledgment is a separate authenticated action.

Migration `20261003_28` was applied to production on the explicit PM release instruction of 2026-10-03. The release reused the existing backend container and tested frontend artifacts without rebuilding images or changing production configuration. Configure SMTP and administrator recipients in the alert page before relying on delivery. Development verification uses isolated server Docker, temporary PostgreSQL and a TLS SMTP sink with test recipients; none of these may mount production data or send production mail. Do not migrate or restart production on a development-only instruction. Preexisting alerts are not retrospectively emailed without an explicit backfill decision.

### External Alert Diagnostics

External alert analysis uses fixed read-only SSH commands against an enabled,
managed asset. A completed collection is not proof of a confirmed root cause or
a healthy server. Failed systemd units are identified from unit state columns,
not from nonempty output, headers, or the zero-unit summary. A missing or denied
optional command may leave incomplete evidence; operators must review the result.
New collections use the updated policy version; historical evidence is unchanged.

The alert page distinguishes completed, running, and failed diagnostic results.
Advancing an alert or converting it to an incident requires a human action and a
reason of at least three characters after trimming whitespace. Connector key
rotation is administrator-only, requires a confirmation, and invalidates the old
key immediately. The replacement secret is displayed only once after success.
Update the sender's configuration before resuming delivery with the new secret.

Validation takes place in isolated Docker containers on the designated server;
test source and build output must not overwrite production bind mounts. The
complex alert-page interaction suite has an explicit 30-second per-test budget
for this environment. Its assertions and production authentication remain
unchanged. Publishing the validated files and restarting services requires the
PM's deployment instruction; no database migration is needed for this correction.

```bash
docker compose --env-file deploy/.env logs -f --tail=200
docker compose --env-file deploy/.env pull
docker compose --env-file deploy/.env up -d --build
docker compose --env-file deploy/.env down
```

`docker compose down` preserves PostgreSQL data. Never add `--volumes` unless permanent data deletion is explicitly intended and a verified backup exists.

## Linux Migration

Copy the repository and the uncommitted `deploy/.env` to the destination using a protected channel. Export and restore PostgreSQL rather than copying live volume files:

```bash
docker compose --env-file deploy/.env exec -T postgres \
  pg_dump -U sentinel -d sentinel -Fc > sentinel.backup

cat sentinel.backup | docker compose --env-file deploy/.env exec -T postgres \
  pg_restore -U sentinel -d sentinel --clean --if-exists
```

Keep the same `CREDENTIAL_MASTER_KEY` during migration; changing or losing it makes existing SSH credentials undecryptable. Rotate the local acceptance token before exposing the application beyond the development host.

## Dedicated Read-Only Linux Diagnostics

This connector is separate from manual SSH and vulnerability scanning. It uses an asset-bound, expiring grant and an AES-256-GCM encrypted dedicated private key. The first implementation accepts literal IPv4/IPv6 asset addresses only. Root, shared manual identities, password authentication, SSH-agent fallback, arbitrary commands and AI writes are not supported. Kubernetes, Docker and recovery verification are separate released connectors documented in their respective sections; real target authorization and SMTP delivery remain deployment prerequisites.

A target administrator must install `deploy/diagnostics/linux_ops_readonly.py` as `/usr/local/libexec/linux_ops_readonly.py`, owned by root and mode 0755. Every parent directory must be root-owned and not group/world writable. Use a separate unprivileged account without sudo, privileged groups, writable system configuration or access to application secrets. Do not automatically install this file on production targets from the platform.

The dedicated public key must be placed in a root-owned, non-user-writable authorized-keys file outside the account's home (for example `/etc/ssh/diagnostic_keys/opsdiag`, directories 0755 and file 0644). An administrator must configure sshd's `AuthorizedKeysFile` for this dedicated account to this file, disable password and keyboard-interactive authentication, PTY, forwarding, tunnels, user rc and user environment, and verify the effective sshd configuration before enabling the grant. The account must not be able to edit/remove these restrictions or add another unrestricted key. No general shell login or SFTP is allowed for this diagnostic identity.

Use this key restriction, replacing only the public key and the optional approved platform source IP:

```text
restrict,command="/usr/bin/python3 -I /usr/local/libexec/linux_ops_readonly.py" ssh-ed25519 PUBLIC_KEY
```

`-I` prevents Python user-site/startup environment from changing the gateway. Restrict by platform source IP with `from="APPROVED_IP"` where practical. Test unknown commands, interactive shell, PTY, SFTP and forwarding denial independently. The gateway accepts only `linuxops-readonly-v1:handshake` and six fixed Linux observation IDs; it does not execute submitted shell text. A handshake is protocol evidence, not proof of arbitrary server-side permission configuration.

In the existing activity-alert page, open the diagnostic authorization tab. A real administrator session must select an enabled asset, a different dedicated identity, fixed observation scope, expiry within 90 days, exact asset-name confirmation and a reason, and attest to manual gateway installation. Create requires the dedicated private key; blank edit retains encrypted material. Responses/audits never reveal private keys. Reconfiguration and revoke use version comparison to prevent stale overwrite.

Register and independently verify the asset host key before granting diagnostics. Address/key changes, expiry, disable/archive, revoke or grant revision changes block collection before further reads; there is no root/manual fallback. Collection uses a 30-second total deadline and bounded streams. Full, partial and failed outcomes are retained with trace-linked audit and administrator result notifications. Runtime evidence does not prove a root cause or recovery, and administrator notification delivery still requires configured SMTP/recipients.

Diagnostic API contract:

- GET `/api/v1/diagnostic-grants?offset=0&limit=50&asset_id=...` returns bounded grants, totals and action availability. GET `/{asset_id}/audit` returns the latest 50 append-only grant/read records.
- POST `/api/v1/diagnostic-grants` requires `asset_id`, dedicated `username`, distinct Linux `action_ids`, timezone-aware `expires_at` within 90 days, `expected_revision`, exact asset-name `confirmation`, reason and `gateway_confirmed=true`. New grants use revision zero and require `private_key`; omit the key to retain existing encrypted material. Outer confirmation whitespace is trimmed; incorrect names are rejected.
- POST `/api/v1/diagnostic-grants/{asset_id}/revoke` requires `expected_revision`, asset-name confirmation and reason. Stale writes return 409. Operator/auditor and emergency Bearer tokens cannot grant or revoke; no delete or private-key-read endpoint exists.
- The action IDs are `linux.uptime`, `linux.memory`, `linux.filesystem`, `linux.inodes`, `linux.failed_units` and `linux.processes`. No process command lines/environment, arbitrary files, supplied shell, repair commands or external model request are collected.
- Mid-collection authorization loss uses `diagnostic_authorization_interrupted`, retaining accepted evidence and attempted revision. Absence of accepted output does not prove no read was sent. Root cause, successful repair and verified recovery must never be inferred from a completed read.

Migration `20261007_30` adds only the encrypted grant and append-only audit tables after `20261004_29`, with asset history protected by RESTRICT foreign keys. PM authorized production publication on October 7, 2026; schema30 and authenticated read-only endpoints are live at `http://192.168.10.17:8088/`. The gateway was tested in isolated Docker, not installed on production targets; target administrators still need to install and authorize it. No diagnostic grants or DEMO evidence were imported into production. Browser visual acceptance remains unverified due to tooling security policy.
