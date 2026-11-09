# Phase 06: Dashboard

The Dashboard is a read-only operational overview backed by `GET /api/v1/dashboard`. It shows asset reachability, vulnerability severity and confirmation state, scan success rate, active knowledge version, recent scans, and the assets with the most findings.

Operators can navigate directly to Scan Tasks, Vulnerability Details, or Knowledge Base. The page cannot alter findings, dispatch remediation, or invoke AI execution.

## Data Trust

- Scan metrics use the same non-archived, non-removed task population as the scan list; this tile is explicitly labeled as scan tasks rather than all running operations.
- Knowledge freshness is aggregated across every configured source. A recent successful source does not make never-synced or failed sources healthy.
- Initial loading, request failures and successful empty results are distinct. Failed requests retain a visible retry prompt rather than presenting default zero counts.
- Global search performs read-only asset, CMDB and CVE queries and carries the selected target into existing page filters. Closing, changing the query or changing users invalidates pending results.
- Administrators configure approved probe CIDRs at API Probes using `GET/PUT /api/v1/probes/policy`. Changes and their audit snapshots commit together; an explicit empty list revokes creation and execution, including existing targets. This requires migration `20261024_43`.
- Patch suggestions with lower, equal or uncertain target ordering cannot be confirmed. Numeric ordering never proves a vendor backport or that remediation succeeded; verification still requires a rescan.

## Candidate Verification (2026-10-24)

This batch was published on 2026-10-24. Verification and release used `/var/tmp/linux-ops-trust-20261024-arCn3Q` on `192.168.10.17`, disposable Docker containers and existing read-only dependencies. The release used a production baseline plus a reviewed whitelist; unrelated candidate changes were excluded.

- Backend target tests: 53 passed; Ruff, TypeScript and the production frontend build passed.
- Frontend target run: 128 of 146 passed initially. Search, six data-trust cases, asset/probe interactions and the two CMDB timeout cases passed targeted reruns after selector and local-timeout fixes. One existing interaction still reached the focused-run default timeout; the whole suite was not rerun and is not claimed green.
- Final production-baseline release package: backend targets 53/53, frontend targets 147/147, Ruff, TypeScript and Vite build passed. Earlier timeout cases passed in this release validation; the existing large-chunk build warning remains.
- Disposable PostgreSQL migration: 42 to 43, back to 42, then 43 passed, preserving earlier audit evidence.
- Browser checks: exact asset search navigation, Windows `Ctrl K`, knowledge-source warnings, and identifiable refresh/scan buttons at 390px. Temporary viewport, preview container and SSH sessions were cleaned up.
- Production checks: migration `20261024_43 (head)`, all three containers healthy, `/api/health=ok`, unauthenticated dashboard/probe-policy/patch/system-status requests return 401, live static hashes match the sanitized build, and frontend/postgres start times remain unchanged. Read-only authenticated smoke confirmed 168 active scan tasks match the dashboard and knowledge coverage is 1/9 with “未完整同步”.
- MFA, granular RBAC, asset-level authorization, login audit investigation, complete setup guides, full mobile/accessibility review and enterprise workflow completion remain outside this batch.

## File Boundaries

Preserve unrelated unpublished work, including service fingerprint enhancements. This release compared the runtime files with production and used a reviewed whitelist; it did not copy the entire candidate archive.

- Frontend navigation/search: `src/App.tsx`, `src/components/AppShell.tsx`, `src/components/GlobalSearch.tsx`, `src/styles.css`.
- Frontend data/configuration: `src/api/patches.ts`, `src/api/probes.ts`, `src/api/system.ts`, `src/types/dashboard.ts`; `src/pages/AssetsPage.tsx`, `CmdbPage.tsx`, `DashboardPage.tsx`, `ObservabilityPage.tsx`, `ReportsPage.tsx`, `BaselinesPage.tsx`, `VulnerabilitiesPage.tsx`, `ProbesPage.tsx`, `PatchesPage.tsx`, `SystemPage.tsx`.
- Backend trust/policy: `src/services/dashboard.py`, `src/schemas/dashboard.py`, `src/routes/system.py`, `src/routes/probes.py`, `src/services/probe_policy.py`, `src/schemas/probe_policy.py`, `src/models/platform_setting.py`, model/migration imports and `migrations/versions/20261024_43_platform_settings.py`.
- Backend patch safety: `src/routes/patches.py`, `src/services/version_safety.py`.
- Target tests accompany the affected modules, including `GlobalSearch.test.tsx`, `AppShell.test.tsx`, `App.navigation.test.tsx`, `DataTrust.test.tsx` and backend dashboard/system/probes/patches/version-safety tests.
- Isolated verification runners: `scripts/verify_data_trust.sh`, `scripts/verify_data_trust_migration.py`. Preview harness files under `.artifacts` are synthetic and must not be published.
