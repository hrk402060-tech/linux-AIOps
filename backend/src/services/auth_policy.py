from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.config import Settings
from src.models.auth import User
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.schemas.auth_policy import AuthPolicyResponse, AuthPolicyUpdate, MfaEnforcement

AUTH_POLICY_KEY = "auth_policy"
AUTH_POLICY_LOCK_KEY = "auth_policy_lock"
logger = logging.getLogger(__name__)


class AuthPolicyUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthPolicy:
    login_failure_threshold: int
    lock_duration_seconds: int
    session_ttl_seconds: int
    require_mfa: bool
    mfa_enforcement: MfaEnforcement
    allow_user_mfa_reset: bool


def defaults(settings: Settings) -> AuthPolicy:
    return AuthPolicy(
        login_failure_threshold=settings.auth_lock_threshold,
        lock_duration_seconds=settings.auth_lock_seconds,
        session_ttl_seconds=settings.auth_session_ttl_seconds,
        require_mfa=False,
        mfa_enforcement="off",
        allow_user_mfa_reset=True,
    )


def resolve(session: Session, settings: Settings) -> AuthPolicy:
    setting = session.get(PlatformSetting, AUTH_POLICY_KEY)
    if setting is None:
        return defaults(settings)
    try:
        payload = AuthPolicyUpdate.model_validate(json.loads(setting.value))
        return AuthPolicy(**payload.model_dump())
    except (TypeError, ValueError) as error:
        logger.error("Invalid authentication policy; password login is unavailable")
        raise AuthPolicyUnavailableError("登录安全策略不可用，请联系管理员检查配置") from error


def read(session: Session, settings: Settings) -> AuthPolicyResponse:
    setting = session.get(PlatformSetting, AUTH_POLICY_KEY)
    policy = resolve(session, settings)
    return AuthPolicyResponse(
        **asdict(policy),
        source="平台配置" if setting is not None else "环境变量",
        updated_by=setting.updated_by if setting is not None else "环境变量",
        updated_at=setting.updated_at if setting is not None else None,
    )


def _is_unique_violation(error: IntegrityError) -> bool:
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return (
        sqlstate == "23505"
        or getattr(original, "args", ())[:1] == (1062,)
        or getattr(original, "sqlite_errorname", None) in {
            "SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE",
        }
    )


def _create_lock_row(session: Session) -> None:
    nested = session.begin_nested()
    try:
        session.add(PlatformSetting(key=AUTH_POLICY_LOCK_KEY, value=""))
        session.flush()
    except IntegrityError as error:
        nested.rollback()
        if not _is_unique_violation(error):
            raise
    else:
        nested.commit()


def _lock_setting(session: Session) -> PlatformSetting | None:
    try:
        # The unique insert serializes first use too; its savepoint preserves
        # the caller's transaction when a competing transaction created it.
        _create_lock_row(session)
        mutex = session.scalar(select(PlatformSetting).where(
            PlatformSetting.key == AUTH_POLICY_LOCK_KEY,
        ).with_for_update().execution_options(populate_existing=True))
        if mutex is None:
            raise SQLAlchemyError("Authentication policy lock row is unavailable")
        return session.scalar(select(PlatformSetting).where(
            PlatformSetting.key == AUTH_POLICY_KEY,
        ).with_for_update().execution_options(populate_existing=True))
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("Authentication policy lock failed: %s", type(error).__name__)
        raise AuthPolicyUnavailableError("登录安全策略锁定失败，请稍后重试") from error


def ensure_mfa_reset_allowed(
    session: Session, settings: Settings, user_id: str,
) -> None:
    ensure_bound_mfa_admin_remains(session, settings, user_id, action="重置")


def is_mfa_required(policy: AuthPolicy, role: str) -> bool:
    return policy.mfa_enforcement == "all" or (
        policy.mfa_enforcement == "admins" and role == "admin"
    )


def can_user_reset_mfa(policy: AuthPolicy, role: str) -> bool:
    return policy.allow_user_mfa_reset and not is_mfa_required(policy, role)


def lock_policy(session: Session, settings: Settings) -> AuthPolicy:
    _lock_setting(session)
    return resolve(session, settings)


def ensure_bound_mfa_admin_remains(
    session: Session, settings: Settings, user_id: str, *, action: str,
) -> None:
    _lock_setting(session)
    if resolve(session, settings).mfa_enforcement == "off":
        return
    bound_admin = (
        User.role == "admin", User.enabled.is_(True), User.mfa_enabled.is_(True),
    )
    target_id = session.scalar(select(User.id).where(
        User.id == user_id, *bound_admin,
    ))
    if target_id is None:
        return
    remaining_id = session.scalar(select(User.id).where(
        User.id != user_id, *bound_admin,
    ).limit(1))
    if remaining_id is None:
        raise ValueError(
            f"强制 MFA 已开启，不能{action}最后一名已绑定 MFA 的启用管理员；"
            "请先关闭强制 MFA，或让另一名管理员绑定 MFA",
        )


def save(
    session: Session,
    settings: Settings,
    payload: AuthPolicyUpdate,
    operator: str,
) -> AuthPolicyResponse:
    setting = _lock_setting(session)
    # Unbound users receive the QR enrollment flow on their next login when
    # enforcement is enabled; forcing a pre-bind would make self-service MFA
    # impossible for existing accounts.
    try:
        previous_snapshot: dict[str, object] | None = read(
            session, settings,
        ).model_dump(mode="json")
    except AuthPolicyUnavailableError:
        # A validated administrator update is the recovery path for a
        # corrupted stored policy; keep the audit explicit without logging
        # the malformed value.
        previous_snapshot = {"status": "unavailable"}
    if setting is None:
        setting = PlatformSetting(key=AUTH_POLICY_KEY)
        session.add(setting)
    setting.value = json.dumps(payload.model_dump(), ensure_ascii=False, separators=(",", ":"))
    setting.updated_by = operator
    setting.updated_at = datetime.now(timezone.utc)
    try:
        session.flush()
        session.add(CmdbChangeAudit(
            id=str(uuid4()),
            entity_type=AUTH_POLICY_KEY,
            entity_id=AUTH_POLICY_KEY,
            action="updated",
            operator_username=operator,
            trace_id=str(uuid4()),
            before_snapshot=previous_snapshot,
            after_snapshot=payload.model_dump(),
        ))
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("Authentication policy transaction failed: %s", type(error).__name__)
        raise ValueError("登录安全策略保存失败，请刷新后重试") from error
    return read(session, settings)
