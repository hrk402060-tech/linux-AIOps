# Stage 1 Acceptance: Credential Management

## Delivered

- React and Ant Design credential management page with desktop and mobile layouts
- Password and SSH private-key credential types
- Create, list, edit, secret replacement, search, filter, refresh, and delete flows
- FastAPI credential API under `/api/v1/credentials`
- Bearer-token access gate for credential administration
- AES-256-GCM authenticated encryption with unique nonces and contextual AAD
- Masked API responses that never return SSH passwords or private keys
- SQLAlchemy model and Alembic migration for PostgreSQL, with SQLite test support

## Security Boundary

- `CREDENTIAL_MASTER_KEY` and `API_ADMIN_TOKEN` are server-side environment secrets.
- The database stores ciphertext, nonce, key version, and metadata only.
- The browser receives only masked credential values after creation or replacement.
- Credential decryption is not exposed through an HTTP endpoint.
- Deleting a credential is irreversible and requires UI confirmation.

## Docker Compose Verification

- Ruff backend static checks: passed
- Backend API and crypto tests: 9 passed
- Project structure tests: 3 passed
- Frontend component tests: 3 passed
- TypeScript production build: passed
- Alembic upgrade and downgrade round trip: passed
- API authentication and encrypted-at-rest integration check: passed
- Playwright desktop 1440 x 900 overflow check: passed
- Playwright mobile 390 x 844 overflow and drawer check: passed

## Acceptance Rule

The next stage, Asset Management, does not start until the PM accepts this stage.
