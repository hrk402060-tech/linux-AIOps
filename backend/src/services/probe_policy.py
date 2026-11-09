import ipaddress
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.services.probe_http import approved_networks

PROBE_POLICY_KEY = "probe_allowed_cidrs"
logger = logging.getLogger(__name__)
_FORBIDDEN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4",
    "240.0.0.0/4", "::/128", "::1/128", "::ffff:0:0/96", "fe80::/10", "ff00::/8",
))


def read_policy(session: Session, fallback: str) -> dict[str, object]:
    setting = session.get(PlatformSetting, PROBE_POLICY_KEY)
    raw = setting.value if setting is not None else fallback
    networks = approved_networks(raw)
    return {
        "allowed_cidrs": [str(item) for item in networks],
        "configured": bool(networks),
        "source": "平台配置" if setting is not None else "环境变量",
        "updated_at": setting.updated_at if setting is not None else None,
    }


def resolve_allowed_cidrs(session: Session, fallback: str) -> str:
    setting = session.get(PlatformSetting, PROBE_POLICY_KEY)
    return setting.value if setting is not None else fallback


def _normalize_policy(raw_value: str) -> list[str]:
    try:
        networks = approved_networks(raw_value.replace("\r", ",").replace("\n", ","))
    except ValueError as exc:
        raise ValueError("批准网段格式无效，请填写 IPv4/IPv6 CIDR") from exc
    if len(networks) > 64:
        raise ValueError("批准网段最多配置 64 项")
    for network in networks:
        if any(network.version == blocked.version and network.overlaps(blocked) for blocked in _FORBIDDEN_NETWORKS):
            raise ValueError("批准网段不得包含回环、元数据、链路本地或组播地址，请缩小范围")
    return list(dict.fromkeys(str(item) for item in networks))


def save_policy(
    session: Session, raw_value: str, operator: str, fallback: str = "",
) -> dict[str, object]:
    networks = _normalize_policy(raw_value)
    normalized = ",".join(networks)
    try:
        previous = read_policy(session, fallback)
        setting = session.get(PlatformSetting, PROBE_POLICY_KEY)
        if setting is None:
            setting = PlatformSetting(key=PROBE_POLICY_KEY)
            session.add(setting)
        setting.value = normalized
        setting.updated_by = operator
        setting.updated_at = datetime.now(timezone.utc)
        result = {
            "allowed_cidrs": networks, "configured": bool(networks),
            "source": "平台配置", "updated_at": setting.updated_at,
        }
        session.add(CmdbChangeAudit(
            id=str(uuid4()), entity_type="probe_policy", entity_id=PROBE_POLICY_KEY,
            action="allowlist_updated", operator_username=operator, trace_id=str(uuid4()),
            before_snapshot={"allowed_cidrs": previous["allowed_cidrs"], "source": previous["source"]},
            after_snapshot={"allowed_cidrs": networks, "source": "平台配置"},
        ))
        session.commit()
        return result
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Probe policy transaction failed")
        raise
