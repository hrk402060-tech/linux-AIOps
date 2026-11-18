from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_authenticated
from src.database import get_session
from src.schemas.ops_assistant import (
    ConversationCreate, ConversationDetail, ConversationList, ConversationUpdate,
    OpsAssistantChatRequest, OpsAssistantChatResponse,
)
from src.services import ops_chat, ops_conversations as conversations
from src.services.asset_access import require_asset, visible_asset_ids

router = APIRouter(prefix="/ops-assistant", tags=["ops-assistant"])


def require_conversation_write(
    principal: AuthPrincipal = Depends(require_authenticated),
) -> AuthPrincipal:
    if principal.must_change_password:
        raise HTTPException(status_code=403, detail="必须先修改临时密码")
    return principal


@router.get("/conversations", response_model=ConversationList)
def read_conversations(
    archived: bool = False, offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=30, ge=1, le=50),
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_authenticated),
) -> ConversationList:
    with conversations.database_errors(session):
        scope = visible_asset_ids(session, principal) if principal.permissions is not None else None
        return conversations.list_owned(session, conversations.owner_key(principal), archived, offset, limit, scope)


@router.post("/conversations", response_model=ConversationDetail, status_code=201)
def create_conversation(
    payload: ConversationCreate, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_conversation_write),
) -> ConversationDetail:
    if principal.permissions is not None and payload.asset_id:
        require_asset(session, principal, payload.asset_id)
    with conversations.database_errors(session):
        owner = conversations.owner_key(principal)
        item = conversations.create(session, owner, payload)
        return conversations.read_detail(session, item.id, owner)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def read_conversation(
    conversation_id: str, offset: int = Query(default=0, ge=0, le=100000),
    limit: int = Query(default=100, ge=1, le=100),
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_authenticated),
) -> ConversationDetail:
    with conversations.database_errors(session):
        _require_conversation_asset(session, principal, conversation_id)
        return conversations.read_detail(
            session, conversation_id, conversations.owner_key(principal), offset, limit,
        )


@router.patch("/conversations/{conversation_id}", response_model=ConversationDetail)
def patch_conversation(
    conversation_id: str, payload: ConversationUpdate,
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_conversation_write),
) -> ConversationDetail:
    with conversations.database_errors(session):
        _require_conversation_asset(session, principal, conversation_id)
        return conversations.update_owned(session, conversation_id, conversations.owner_key(principal), payload)


@router.post("/chat", response_model=OpsAssistantChatResponse, status_code=201)
def chat_operations_assistant(
    payload: OpsAssistantChatRequest, request: Request,
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_conversation_write),
) -> OpsAssistantChatResponse:
    with conversations.database_errors(session):
        if payload.asset_id and principal.permissions is not None:
            require_asset(session, principal, payload.asset_id)
        if payload.conversation_id:
            _require_conversation_asset(session, principal, str(payload.conversation_id))
        return ops_chat.chat(
            session, conversations.owner_key(principal), payload, request.app.state.credential_vault,
        )


def _require_conversation_asset(session: Session, principal: AuthPrincipal, conversation_id: str) -> None:
    if principal.permissions is not None:
        item = conversations.get_owned(session, conversation_id, conversations.owner_key(principal))
        if item.asset_id:
            require_asset(session, principal, item.asset_id)
