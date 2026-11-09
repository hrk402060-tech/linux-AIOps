from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset_access import UserAssetAccess
from src.models.auth import User
from src.models.diagnostic_job import DiagnosticJob
from src.services.diagnostic_grants import DiagnosticAuthorizationError

SYSTEM_OPERATORS = frozenset({"system", "scheduler", "break-glass-admin"})


def require_operator_scope(session: Session, job: DiagnosticJob) -> None:
    """Keep manually requested jobs bound to the requester's current asset scope."""
    if job.trigger_type != "manual" or job.operator in SYSTEM_OPERATORS:
        return
    user = session.scalar(select(User).where(User.username == job.operator))
    # Older jobs may outlive their user row; keep those records claimable for
    # evidence retention while current session-created jobs remain bound.
    if user is None:
        return
    if (
        not user.enabled
        or user.must_change_password
        or user.role not in {"admin", "operator"}
    ):
        raise DiagnosticAuthorizationError("Diagnostic requester is no longer active")
    if user.role == "admin" or user.asset_scope == "all":
        return
    assigned = session.scalar(select(UserAssetAccess.id).where(
        UserAssetAccess.user_id == user.id,
        UserAssetAccess.asset_id == job.asset_id,
        UserAssetAccess.can_view.is_(True),
    ))
    if assigned is None:
        raise DiagnosticAuthorizationError("Diagnostic requester lost asset access")
