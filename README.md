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

## 运行环境

- Linux servers
- Docker Engine and Docker Compose v2
- UTF-8 files with LF line endings
- Python 3.12 backend and scanner
- React 18 with TypeScript and Ant Design
- PostgreSQL

## Docker Deployment

The current stage runs as a three-service Compose stack named `linux-aiops-platform`: React/Nginx, FastAPI, and PostgreSQL. See [docs/deployment.md](docs/deployment.md) for Docker Compose startup, backup, and Linux deployment instructions.

### Docker Compose 快速部署

运行环境只需要 Linux、Docker Engine 和 Docker Compose v2，不需要 WSL 或本地开发环境。

```bash
git clone https://github.com/hrk402060-tech/linux-AIOps.git
cd linux-AIOps
cp deploy/.env.example deploy/.env
```

编辑 `deploy/.env`，至少替换以下配置：

| 配置项 | 用途 |
| --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL 数据库密码 |
| `CREDENTIAL_MASTER_KEY` | AES-256-GCM 凭据加密主密钥，使用 `openssl rand -base64 32` 生成 |
| `API_ADMIN_TOKEN` | 部署校验和紧急恢复令牌，至少 32 个字符 |
| `AUTH_BOOTSTRAP_USERNAME` | 首次管理员账号 |
| `AUTH_BOOTSTRAP_PASSWORD` | 首次管理员临时密码，首次登录后必须修改 |
| `APP_PORT` | Web 端口，默认 `8080`；需要 `8088` 时填写 `8088` |
| `ALLOWED_ORIGINS` | IP 访问填写 `auto`；HTTPS 生产环境建议填写准确的 HTTPS 来源 |

生成随机值示例：

```bash
openssl rand -hex 32
openssl rand -base64 32
```

启动服务并检查状态：

```bash
docker compose --env-file deploy/.env up -d --build
docker compose --env-file deploy/.env ps
curl http://127.0.0.1:${APP_PORT:-8080}/api/health
```

浏览器访问 `http://服务器IP:${APP_PORT:-8080}/`。AI 服务地址、模型和 API 密钥由管理员登录后在“系统管理 → AI 配置”中维护，不需要写入 `.env`。`deploy/.env` 只保留在服务器，不能提交到 GitHub。
