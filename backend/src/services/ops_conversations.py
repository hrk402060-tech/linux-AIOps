from datetime import datetime, timedelta, timezone
from collections.abc import Iterator
from contextlib import contextmanager
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.ops_assistant import OpsConversation, OpsMessage
from src.schemas.ops_assistant import (
    ConversationCreate, ConversationDetail, ConversationList, ConversationSummary,
    ConversationUpdate, SavedMessage,
)
from src.services.ops_redaction import redact

logger = logging.getLogger(__name__)
LEASE_SECONDS = 180


@contextmanager
def database_errors(session: Session) -> Iterator[None]:
    try:
        yield
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("Operations conversation transaction failed (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="会话保存失败，请稍后刷新确认") from error


def owner_key(principal: AuthPrincipal) -> str:
    if principal.user_id:
        return f"user:{principal.user_id}"
    if principal.auth_type == "break_glass":
        return "break_glass:admin"
    raise HTTPException(status_code=401, detail="请重新登录")


def get_owned(session: Session, conversation_id: str, owner: str) -> OpsConversation:
    item = session.scalar(select(OpsConversation).where(
        OpsConversation.id == conversation_id, OpsConversation.owner_key == owner,
    ).execution_options(populate_existing=True))
    if item is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return item


def require_enabled_asset(session: Session, asset_id: str | None) -> Asset | None:
    if asset_id is None:
        return None
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise HTTPException(status_code=409, detail="停用或归档资产不能作为运维助手上下文")
    return asset


def create(session: Session, owner: str, payload: ConversationCreate) -> OpsConversation:
    require_enabled_asset(session, payload.asset_id)
    item = OpsConversation(
        id=str(uuid4()), owner_key=owner, title="新会话", asset_id=payload.asset_id,
        redacted_context=redact(payload.context.strip()),
    )
    session.add(item)
    commit(session)
    return item


def list_owned(
    session: Session, owner: str, archived: bool, offset: int, limit: int,
) -> ConversationList:
    condition = (
        OpsConversation.owner_key == owner, OpsConversation.archived == archived,
    )
    total = session.scalar(select(func.count()).select_from(OpsConversation).where(*condition)) or 0
    items = session.scalars(select(OpsConversation).where(*condition).order_by(
        OpsConversation.updated_at.desc(), OpsConversation.id.desc(),
    ).offset(offset).limit(limit))
    return ConversationList(
        items=[ConversationSummary.model_validate(item) for item in items], total=total,
    )


def read_detail(
    session: Session, conversation_id: str, owner: str, offset: int = 0, limit: int = 100,
) -> ConversationDetail:
    item = get_owned(session, conversation_id, owner)
    condition = OpsMessage.conversation_id == item.id
    total = session.scalar(select(func.count()).select_from(OpsMessage).where(condition)) or 0
    messages = list(session.scalars(select(OpsMessage).where(condition).order_by(
        OpsMessage.number.desc(),
    ).offset(offset).limit(limit)))
    return ConversationDetail(
        **ConversationSummary.model_validate(item).model_dump(),
        context=item.redacted_context,
        messages=[SavedMessage.model_validate(message) for message in reversed(messages)],
        total_messages=total,
    )


def claim(session: Session, item: OpsConversation, request_id: str, allow_archived: bool = False) -> None:
    now = datetime.now(timezone.utc)
    conditions = [
        OpsConversation.id == item.id, OpsConversation.owner_key == item.owner_key,
        or_(OpsConversation.active_request_id.is_(None),
            OpsConversation.request_started_at < now - timedelta(seconds=LEASE_SECONDS)),
    ]
    if not allow_archived:
        conditions.append(OpsConversation.archived.is_(False))
    result = session.execute(update(OpsConversation).where(*conditions).values(
        active_request_id=request_id, request_started_at=now,
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="会话正在处理请求或已归档，请稍后刷新")
    session.execute(update(OpsMessage).where(
        OpsMessage.conversation_id == item.id, OpsMessage.status == "pending",
    ).values(status="failed", error_message="上次请求已中断，请重新提问"))


def update_owned(
    session: Session, conversation_id: str, owner: str, payload: ConversationUpdate,
) -> ConversationDetail:
    item = get_owned(session, conversation_id, owner)
    claim(session, item, str(uuid4()), allow_archived=True)
    values: dict[str, object] = {
        "active_request_id": None, "request_started_at": None,
        "updated_at": datetime.now(timezone.utc),
    }
    if payload.title is not None:
        values["title"] = redact(payload.title)[:160]
    if payload.archived is not None:
        values["archived"] = payload.archived
    session.execute(update(OpsConversation).where(OpsConversation.id == item.id).values(
        **values,
    ).execution_options(synchronize_session=False))
    commit(session)
    session.expire_all()
    return read_detail(session, conversation_id, owner)


def commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("Operations conversation write failed (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="会话保存失败，请稍后刷新确认") from error
