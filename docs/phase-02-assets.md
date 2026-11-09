# Stage 2 Design: Asset Management

## Scope

The Asset Management stage delivers one React page and `/api/v1/assets` APIs. It does not start vulnerability scans or execute commands on managed hosts.

## Data Model

Each asset stores a unique display name, hostname or IP address, SSH port, operating-system family, environment group, optional credential reference, labels, status, and last connectivity result. Credentials remain encrypted in the credential table and are referenced only by ID.

Supported operating-system families include Ubuntu, Debian, RHEL, CentOS, Rocky Linux, AlmaLinux, openEuler, Kylin Linux, UnionTech UOS, Anolis OS, automatic detection, and other Linux distributions. Exact edition and package-manager facts will be collected read-only during the Scan Tasks stage.

## API Contract

- `GET /api/v1/assets` lists assets with masked credential metadata.
- `POST /api/v1/assets` creates an asset.
- `PATCH /api/v1/assets/{id}` updates asset metadata.
- `DELETE /api/v1/assets/{id}` deletes an asset after UI confirmation.
- `POST /api/v1/assets/{id}/check` performs a bounded TCP connection to the configured SSH endpoint.

Every endpoint requires the same administrative bearer token used by Credential Management. Host input is validated as an IP address or DNS hostname. Connectivity checks use a fixed timeout and never authenticate or run remote commands.

## UI Contract

The page provides summary metrics, search, status and operating-system filters, asset creation/editing, credential selection, deletion confirmation, and a manual connectivity check. Desktop and mobile layouts must remain free of horizontal page overflow; the table may scroll inside its own frame.

## Acceptance Boundary

Stage 3, Knowledge Base, does not start until the PM accepts the Asset Management page and API.

## WSL Docker Verification

- Ruff backend static checks: passed
- Backend credential and asset API tests: 15 passed
- Project structure and deployment contract tests: 8 passed
- Frontend component tests: 6 passed
- TypeScript and Vite production build: passed
- Alembic SQLite upgrade, downgrade, and re-upgrade: passed
- PostgreSQL incremental migration to `20260903_02`: passed
- Docker authentication, masked credential, encrypted storage, and asset creation: passed
- Playwright desktop 1440 x 900 overflow check: passed
- Playwright mobile 390 x 844 page and drawer checks: passed
