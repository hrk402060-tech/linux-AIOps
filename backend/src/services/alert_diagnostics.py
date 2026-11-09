import paramiko
import json
import time
from collections.abc import Callable

from cryptography.exceptions import InvalidTag
from sqlalchemy.orm import Session

from src.services import diagnostic_grants, diagnostic_transport

from src.models.asset import Asset
from src.services.crypto import CredentialVault
from src.services.alert_security import redact_text

POLICY_VERSION = "readonly-linux-2026.10.07"
COMMANDS = {
    "uptime": "uptime",
    "memory": "free -m",
    "filesystem": "df -P -x tmpfs -x devtmpfs",
    "failed_units": "LC_ALL=C systemctl --failed --no-legend --no-pager --plain 2>/dev/null || true",
    "kernel_errors": "dmesg --level=err,warn 2>/dev/null | tail -n 80 || true",
}
MAX_EVIDENCE_CHARS = 16_000


class DiagnosticAuthorizationRequired(diagnostic_grants.DiagnosticAuthorizationError):
    def __init__(self, message: str, revision: int | None = None,
                 evidence: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.revision = revision
        self.evidence = evidence or {}


class DiagnosticCollectionFailed(RuntimeError):
    def __init__(self, revision: int, code: str = 'transport_failed') -> None:
        super().__init__('Restricted diagnostic collection failed')
        self.revision = revision
        self.code = code if code in diagnostic_transport.FAILURE_CODES else 'transport_failed'
        self.retryable = self.code in diagnostic_transport.TRANSIENT_CODES


class DiagnosticReadInterrupted(RuntimeError):
    def __init__(self, message: str, revision: int | None = None,
                 evidence: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.revision = revision
        self.evidence = evidence or {}


def require_authorization(asset: Asset, session: Session | None = None) -> None:
    if session is None:
        raise DiagnosticAuthorizationRequired('Dedicated diagnostic authorization unavailable')
    try:
        diagnostic_grants.require_grant(session, asset)
    except diagnostic_grants.DiagnosticAuthorizationError as exc:
        raise DiagnosticAuthorizationRequired('Dedicated diagnostic authorization unavailable') from exc


def collect(
    asset: Asset, vault: CredentialVault, expected_host_key: paramiko.PKey | None = None,
    session: Session | None = None,
    guard: Callable[[], None] | None = None, expected_revision: int | None = None,
) -> dict[str, object]:
    del expected_host_key
    if guard is not None:
        guard()
    require_authorization(asset, session)
    try:
        grant = diagnostic_grants.require_grant(session, asset, expected_revision)
    except diagnostic_grants.DiagnosticAuthorizationError as exc:
        raise DiagnosticAuthorizationRequired('Bound authorization unavailable', expected_revision) from exc
    deadline = time.monotonic() + diagnostic_transport.TOTAL_SECONDS
    authorize = _authorization_guard(session, asset, grant.revision, guard)
    client = None
    try:
        client = diagnostic_transport._connect(asset, grant, vault, deadline, authorize)
        diagnostic_transport.handshake(client, grant, deadline, authorize)
        evidence, errors = _observe(client, grant.action_ids, deadline, authorize)
        authorize()
        _raise_empty_failure(evidence, errors, grant.revision)
    except (DiagnosticAuthorizationRequired, DiagnosticReadInterrupted) as exc:
        exc.revision = grant.revision
        if 'evidence' in locals():
            exc.evidence.update(evidence)
        raise
    except (OSError, paramiko.SSHException, ValueError, InvalidTag,
            diagnostic_transport.DiagnosticTransportError) as exc:
        raise DiagnosticCollectionFailed(grant.revision, diagnostic_transport.failure_code(exc)) from exc
    finally:
        if client is not None:
            client.close()
    status = 'failed' if not evidence else 'partial' if errors else 'completed'
    summary = _structured_summary(evidence, errors)
    return {'status': status, 'summary': summary, 'evidence': {
        'action_errors': json.dumps(errors), 'grant_revision': str(grant.revision),
        'collection_mode': 'restricted_read_only_ssh', 'remediation': 'disabled',
        'read_only': 'true', 'policy_version': POLICY_VERSION,
        **evidence,
    }}


def _authorization_guard(session: Session, asset: Asset, revision: int,
                         guard: Callable[[], None] | None) -> Callable[[], None]:
    def authorize() -> None:
        if guard is not None:
            guard()
        try:
            diagnostic_grants.require_grant(session, asset, revision)
        except diagnostic_grants.DiagnosticAuthorizationError as exc:
            raise DiagnosticAuthorizationRequired('Authorization interrupted', revision) from exc
        if guard is not None:
            guard()
    return authorize


def _raise_empty_failure(evidence: dict[str, str], errors: dict[str, str], revision: int) -> None:
    if evidence or not errors:
        return
    terminal = next((code for code in errors.values()
                     if code not in diagnostic_transport.TRANSIENT_CODES), None)
    raise DiagnosticCollectionFailed(revision, terminal or next(iter(errors.values())))


def sanitize_evidence(value: dict[str, object]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    remaining = MAX_EVIDENCE_CHARS
    required = {'grant_revision', 'action_errors', 'policy_version', 'collection_mode',
                'remediation', 'read_only', 'error_code', 'execution_performed'}
    ordered = sorted(value.items(), key=lambda pair: pair[0] not in required)
    for name, output in ordered:
        redacted = redact_text(str(output))[:2048] if name in required else redact_text(str(output))
        evidence[str(name)] = redacted[:remaining]
        remaining = max(0, remaining - len(evidence[str(name)]))
    return evidence


def _observe(client: paramiko.SSHClient, actions: list[str], deadline: float,
             authorize: Callable[[], None]) -> tuple[dict[str, str], dict[str, str]]:
    evidence: dict[str, str] = {}
    errors: dict[str, str] = {}
    for action in actions:
        try:
            authorize()
            result = diagnostic_transport.request(client, action, deadline, authorize)
            if result.get('status') != 'completed' or not isinstance(result.get('evidence'), dict):
                raise diagnostic_transport.DiagnosticTransportError('invalid_gateway_response')
            evidence[action] = json.dumps(result['evidence'], ensure_ascii=True)
        except (DiagnosticAuthorizationRequired, DiagnosticReadInterrupted) as exc:
            exc.evidence.update(evidence)
            raise
        except (OSError, paramiko.SSHException, diagnostic_transport.DiagnosticTransportError) as exc:
            errors[action] = diagnostic_transport.failure_code(exc)
    return evidence, errors


def _structured_summary(evidence: dict[str, str], errors: dict[str, str]) -> str:
    if not evidence:
        return '只读诊断未取得有效证据，需管理员人工排查。'
    prefix = '部分读取失败；' if errors else ''
    units = json.loads(evidence.get('linux.failed_units', '{}')).get('failed_units', [])
    if units:
        return prefix + '只读诊断发现失败的 systemd 单元，需人工确认服务影响与根因。'
    return prefix + '已取得授权范围的 Linux 运行证据，根因与恢复仍需人工核实。'


def _summarize(evidence: dict[str, str]) -> str:
    failed = evidence.get("failed_units", "").strip()
    kernel = evidence.get("kernel_errors", "").strip()
    if _has_failed_unit(failed):
        return "只读诊断发现失败的 systemd 单元，需人工确认服务影响。"
    if kernel:
        return "只读诊断发现内核警告或错误，需人工研判。"
    return "固定白名单只读诊断未发现明确根因，需结合告警上下文人工研判。"


def _has_failed_unit(output: str) -> bool:
    for line in output.splitlines():
        columns = line.lstrip(" \t\u25cf").split()
        if len(columns) < 4:
            continue
        unit, load, active, sub = columns[:4]
        if "." in unit and load in {"loaded", "not-found", "error", "bad-setting", "masked"}:
            if active == "failed" or sub == "failed":
                return True
    return False
