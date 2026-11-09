from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.ops_assistant import OpsConversation, OpsMessage
from src.schemas.ops_assistant import ConversationCreate, OpsAssistantChatRequest, OpsAssistantChatResponse
from src.services import ops_conversations as conversations, terminal_assistant
from src.services.ai_settings import AISettingsError, terminal_runtime_config
from src.services.crypto import CredentialVault
from src.services.ops_redaction import redact

logger = logging.getLogger(__name__)
MODEL_ERROR = "运维模型请求失败，请检查 AI 配置或稍后重试"


@dataclass(frozen=True)
class PendingChat:
    conversation_id: str
    request_id: str
    message_id: str
    number: int
    question: str
    context: str
    history: list[dict[str, str]]
    sources: list[str]


def _asset_context(asset: Asset | None, context: str) -> str:
    if asset is None:
        return context
    summary = (
        f"资产名称：{asset.name}\n主机地址：{asset.host}:{asset.ssh_port}\n"
        f"系统类型：{asset.os_family.value}\n环境：{asset.environment.value}\n"
    )
    return f"{summary}\n用户脱敏证据：\n{context}" if context else summary


def _replay(
    session: Session, conversation_id: str, request_id: str, question: str,
) -> OpsAssistantChatResponse | None:
    messages = list(session.scalars(select(OpsMessage).where(
        OpsMessage.conversation_id == conversation_id, OpsMessage.request_id == request_id,
    )))
    if not messages:
        return None
    user = next((message for message in messages if message.role == "user"), None)
    if user is None or user.content != question:
        raise HTTPException(status_code=409, detail="请求标识已被使用，请新建一次提问")
    assistant = next((message for message in messages if message.role == "assistant"), None)
    if assistant and assistant.analysis:
        return OpsAssistantChatResponse.model_validate(assistant.analysis)
    if user.status == "pending":
        _expire_abandoned_request(session, conversation_id, request_id)
    detail = "该请求正在处理中，请稍后刷新" if user.status == "pending" else "该请求已失败，请重新发送"
    raise HTTPException(status_code=409, detail=detail)


def _expire_abandoned_request(session: Session, conversation_id: str, request_id: str) -> None:
    now = datetime.now(timezone.utc)
    result = session.execute(update(OpsConversation).where(
        OpsConversation.id == conversation_id, OpsConversation.active_request_id == request_id,
        OpsConversation.request_started_at < now - timedelta(seconds=conversations.LEASE_SECONDS),
    ).values(
        active_request_id=None, request_started_at=None, updated_at=now,
    ).execution_options(synchronize_session=False))
    if result.rowcount == 1:
        session.execute(update(OpsMessage).where(
            OpsMessage.conversation_id == conversation_id, OpsMessage.request_id == request_id,
            OpsMessage.status == "pending",
        ).values(status="failed", error_message="上次请求已中断，请重新提问"))
        conversations.commit(session)
        raise HTTPException(status_code=409, detail="上次请求已中断，请重新提问")
    session.rollback()


def _history(session: Session, conversation_id: str) -> list[dict[str, str]]:
    messages = list(session.scalars(select(OpsMessage).where(
        OpsMessage.conversation_id == conversation_id, OpsMessage.status == "completed",
    ).order_by(OpsMessage.number.desc()).limit(20)))
    return [{"role": item.role, "content": item.content} for item in reversed(messages)]


def _prepare(
    session: Session, item: OpsConversation, payload: OpsAssistantChatRequest, request_id: str,
) -> PendingChat:
    asset = conversations.require_enabled_asset(session, item.asset_id)
    sources = ["用户问题"]
    if item.redacted_context:
        sources.append("用户补充的脱敏证据")
    if asset:
        sources.append(f"服务器资产：{asset.name}")
    conversations.claim(session, item, request_id)
    number = (session.scalar(select(func.max(OpsMessage.number)).where(
        OpsMessage.conversation_id == item.id,
    )) or 0) + 1
    question = redact(payload.message)[:4000]
    pending = PendingChat(
        item.id, request_id, str(uuid4()), number, question,
        _asset_context(asset, item.redacted_context), _history(session, item.id), sources,
    )
    session.add(OpsMessage(
        id=pending.message_id, conversation_id=item.id, number=number,
        request_id=request_id, role="user", content=question, status="pending",
    ))
    values: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if item.title == "新会话":
        values["title"] = question[:160]
    session.execute(update(OpsConversation).where(OpsConversation.id == item.id).values(
        **values,
    ).execution_options(synchronize_session=False))
    conversations.commit(session)
    return pending


def _finish(
    session: Session, pending: PendingChat,
    response: OpsAssistantChatResponse | None, error: str | None = None,
) -> None:
    # The lease CAS makes an old model response unable to overwrite a successor request.
    result = session.execute(update(OpsConversation).where(
        OpsConversation.id == pending.conversation_id,
        OpsConversation.active_request_id == pending.request_id,
    ).values(
        active_request_id=None, request_started_at=None, updated_at=datetime.now(timezone.utc),
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="会话请求已失效，请刷新后继续")
    session.execute(update(OpsMessage).where(OpsMessage.id == pending.message_id).values(
        status="completed" if response else "failed", error_message=error,
    ).execution_options(synchronize_session=False))
    if response:
        session.add(OpsMessage(
            id=str(uuid4()), conversation_id=pending.conversation_id, number=pending.number + 1,
            request_id=pending.request_id, role="assistant", content=response.reply,
            status="completed", analysis=response.model_dump(mode="json"),
        ))
    conversations.commit(session)


def _response(pending: PendingChat, reply: str, engine: str) -> OpsAssistantChatResponse:
    diagnosis, plan, validations, checks = terminal_assistant._local_analysis(pending.question)
    raw = {
        "conversation_id": pending.conversation_id, "engine": engine, "reply": reply,
        "diagnosis": diagnosis, "plan": plan, "validation_steps": validations,
        "read_only": True, "context_sources": pending.sources,
        "read_only_checks": [
            {"command": command, "purpose": purpose, "validation_step": validation}
            for command, purpose, validation in checks
        ],
        "history": [],
    }
    return OpsAssistantChatResponse.model_validate(_redact_value(raw))


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def chat(
    session: Session, owner: str, payload: OpsAssistantChatRequest, vault: CredentialVault,
) -> OpsAssistantChatResponse:
    item = (
        conversations.get_owned(session, str(payload.conversation_id), owner)
        if payload.conversation_id else conversations.create(session, owner, ConversationCreate(
            asset_id=payload.asset_id, context=payload.context,
        ))
    )
    if item.archived:
        raise HTTPException(status_code=409, detail="归档会话不可继续提问")
    request_id = str(payload.request_id or uuid4())
    question = redact(payload.message)[:4000]
    replay = _replay(session, item.id, request_id, question)
    if replay:
        return replay
    pending = _prepare(session, item, payload, request_id)
    return _run_model(session, pending, vault)


def _run_model(session: Session, pending: PendingChat, vault: CredentialVault) -> OpsAssistantChatResponse:
    try:
        configuration = terminal_runtime_config(session, vault)
        session.rollback()  # Do not keep a database transaction open during model inference.
        reply, engine = terminal_assistant.chat_reply(
            configuration, pending.question, pending.context, pending.history,
        )
        response = _response(pending, reply, engine)
    except Exception as error:
        logger.warning("Operations model request failed (%s)", type(error).__name__)
        detail = (
            "运维模型配置不可用，已切换本地只读规则"
            if isinstance(error, AISettingsError) else "外部运维模型暂不可用，已切换本地只读规则"
        )
        fallback_reply, engine = terminal_assistant.local_fallback_reply(
            pending.question, pending.context, pending.history,
        )
        fallback = _response(
            pending,
            f"{detail}。以下建议不代表已连接服务器、已执行操作或已恢复服务。\n\n{fallback_reply}",
            engine,
        )
        _finish(session, pending, fallback)
        return fallback
    _finish(session, pending, response)
    return response
