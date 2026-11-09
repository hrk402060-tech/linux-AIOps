# Linux 智能综合运维平台

This repository is the Linux Intelligent Integrated Operations Platform. Its current implementation is the Security Operations Center slice of the wider operations product, with reusable asset, credential, task, audit, Web SSH, vulnerability, knowledge, and controlled AI capabilities. Operations is the primary product line; security capabilities must remain reusable platform services rather than define the whole product.

## Status

The approved Linux platform MVP is implemented as a Docker Compose stack on a Linux host. The current production authentication revision is `20260915_21`.

## Product Scope

The web console covers the planned module groups:

1. Unified workspace
2. Asset and resource center
3. Vulnerability detection and governance
4. Baseline and compliance
5. Exposure surface and credential risk
6. Security events and alerts
7. Automated operations and Web SSH
8. Patch and change governance
9. Task scheduling
10. Reports and unified audit
11. Knowledge base and AI
12. System administration

The platform supports network discovery and authorized, read-only SSH assessment. It does not exploit vulnerabilities or modify target systems by default. The AI Agent uses local inference, keeps data in-domain, and cannot execute remediation without explicit human approval.

## Security Requirements

- Vendor security advisories take precedence over generic version matching.
- Findings that cannot reliably account for vendor backports are marked for manual confirmation.
- CNVD and CNNVD data use signed offline packages as the primary ingestion path. Invalid signatures or checksums cause the entire import to be rejected.
- SSH credentials are encrypted at rest with AES-256-GCM. Keys remain server-side, and browsers receive masked values only.
- Knowledge base releases are versioned, observable, and reversible.
- Scan tasks follow an auditable state machine and support reruns without overwriting previous runs.
- Interactive users sign in with username and password. Passwords use Argon2id, browser sessions use HttpOnly cookies, and the legacy administrator token is reserved for break-glass operations.

## Planned Components

```text
frontend/   React and Ant Design web console
backend/    FastAPI management API
scanner/    Independent read-only scan worker
src/        Shared project entry and metadata
tests/      Project and integration tests
docs/       Product and architecture documentation
config/     Non-secret configuration templates
```

## Development

Development, builds, and tests use the repository toolchain. Python packages use the trusted Aliyun mirror through `uv.toml`; frontend packages use npmmirror through `frontend/.npmrc`.

Install and test the backend:

```bash
uv sync --group dev --link-mode copy
.venv/bin/ruff check backend/src backend/tests backend/migrations
cd backend && ../.venv/bin/pytest -q
```

Install, test, and build the frontend:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm test
pnpm run build
```

Run the project structure check:

```bash
python -m unittest discover -s tests -v
```

Copy `backend/.env.example` to a server-side environment configuration before starting the API. Generate independent secrets for the credential master key, initial administrator password, and break-glass token. Never commit these values.

## Target Environment

- Linux servers
- UTF-8 files with LF line endings
- Python 3.12 backend and scanner
- React 18 with TypeScript and Ant Design
- PostgreSQL

## Docker Deployment

The current stage runs as a three-service Compose stack named `linux-aiops-platform`: React/Nginx, FastAPI, and PostgreSQL. See [docs/deployment.md](docs/deployment.md) for Docker Compose startup, backup, and Linux deployment instructions.
