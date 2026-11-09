from datetime import datetime, timezone
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.services.ai_settings import TerminalAIConfig
from src.services.ai_http import open_model_response as urlopen
from src.models.terminal import TerminalAssistantAnalysis, TerminalCommandAudit, TerminalSession
from src.schemas.terminal_assistant import TerminalAssistantRequest

SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*([^\s]+)"),
    re.compile(r"(?i)(authorization:\s*bearer)\s+[^\s]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)
SERVICE_PATTERN = re.compile(r"(?<![A-Za-z0-9_.@-])([A-Za-z0-9_.@-]+\.service)")


class TerminalAssistantError(ValueError):
    pass


class TerminalAssistantReadOnlyError(TerminalAssistantError):
    pass


def analyze(session: Session, payload: TerminalAssistantRequest) -> tuple[TerminalAssistantAnalysis, list[TerminalCommandAudit]]:
    terminal = session.get(TerminalSession, payload.session_id)
    if terminal is None:
        raise TerminalAssistantError("终端会话不存在")
    if terminal.status not in {"waiting", "connected"}:
        raise TerminalAssistantError("仅等待连接或已连接会话可以使用终端助手")
    context = redact_context(payload.context)
    decision_context = payload.question.strip() or context
    diagnosis, plan, validations, _ = _local_analysis(decision_context)
    analysis = TerminalAssistantAnalysis(
        id=str(uuid4()), terminal_session_id=terminal.id, mode="analysis_only",
        redacted_context=context, diagnosis=diagnosis, plan=plan,
        validation_steps=validations, operator=payload.operator.strip(),
    )
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis, []


def redact_context(value: str) -> str:
    redacted = value[-12000:]
    redacted = SECRET_PATTERNS[0].sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    redacted = SECRET_PATTERNS[1].sub(lambda match: f"{match.group(1)} [REDACTED]", redacted)
    return SECRET_PATTERNS[2].sub("[REDACTED PRIVATE KEY]", redacted)


def list_commands(session: Session, terminal_session_id: str) -> list[TerminalCommandAudit]:
    return list(session.scalars(select(TerminalCommandAudit).where(
        TerminalCommandAudit.terminal_session_id == terminal_session_id
    ).order_by(TerminalCommandAudit.created_at.desc())))


def review_command(session: Session, item: TerminalCommandAudit, decision: str, operator: str, confirmation: str) -> TerminalCommandAudit:
    if decision != "rejected":
        raise TerminalAssistantReadOnlyError("AI 仅允许只读分析，不允许批准或下发命令")
    if item.status != "pending_confirmation":
        raise TerminalAssistantError("该命令不在待确认状态")
    if confirmation.strip() != item.confirmation_phrase:
        raise TerminalAssistantError("命令确认文本不匹配")
    item.status = decision
    item.reviewed_by = operator.strip()
    item.reviewed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(item)
    return item


def begin_execution(session: Session, terminal_session_id: str, command_id: str) -> TerminalCommandAudit:
    raise TerminalAssistantReadOnlyError("AI 命令执行已禁止；历史批准命令同样不能下发")


def finish_execution(session: Session, item: TerminalCommandAudit, succeeded: bool) -> None:
    raise TerminalAssistantReadOnlyError("AI 命令执行已禁止")


def _local_analysis(context: str) -> tuple[str, list[str], list[str], list[tuple[str, str, str]]]:
    lowered = context.lower()
    if any(term in lowered for term in ("安装 k8s", "安装k8s", "kubernetes", "k8s")):
        return (
            "你要求安装 Kubernetes。先检查系统资源、容器运行时和网络，不直接执行安装。",
            ["确认发行版与内核", "确认 CPU、内存、磁盘和网络", "确认容器运行时", "人工确认安装方案后再执行"],
            ["确认 kubelet、kubeadm 和 kubectl 版本", "确认节点状态为 Ready"],
            [("cat /etc/os-release && uname -r", "确认系统版本", "核对安装兼容性"),
             ("nproc && free -h && df -h /", "检查资源余量", "确认满足最低资源要求"),
             ("command -v containerd || command -v docker", "检查容器运行时", "确认已有运行时")],
        )
    if any(term in lowered for term in ("ssh", "sshd", "远程登录", "远程连接", "ssh服务器", "ssh 服务器")):
        return (
            "这是 SSH 服务查看/连接问题，建议从服务状态、监听端口和生效配置三方面核对。",
            ["确认 sshd 是否运行", "确认 SSH 监听端口", "核对认证策略与资产配置是否一致"],
            ["复查 SSH 服务状态", "确认目标端口可达并使用正确认证方式"],
            [("systemctl status sshd --no-pager", "查看 SSH 服务状态", "确认 sshd 正常运行"),
             ("ss -lntp | grep -E ':(22|2222)\\b'", "查看 SSH 监听端口", "确认目标端口正在监听"),
             ("sshd -T 2>/dev/null | grep -E '^(port|listenaddress|permitrootlogin|passwordauthentication) '", "查看生效认证配置", "核对端口和认证策略")],
        )
    if "no space left" in lowered or ("disk" in lowered and "full" in lowered):
        return (
            "检测到磁盘空间不足迹象。",
            ["确认文件系统使用率", "定位占用较大的目录", "确认业务影响后再制定清理方案"],
            ["再次检查目标文件系统使用率", "确认关键服务状态"],
            [("df -hT", "查看文件系统容量与类型", "确认目标挂载点使用率"),
             ("du -xhd1 /var 2>/dev/null | sort -h | tail", "定位 /var 下高占用目录", "核对是否为日志或业务数据")],
        )
    service = SERVICE_PATTERN.search(context)
    if "systemd" in lowered or "service failed" in lowered or service:
        name = service.group(1) if service else None
        commands = [("systemctl --failed --no-pager", "列出失败服务", "确认失败单元名称")]
        if name:
            commands.append((f"journalctl -u {name} -n 100 --no-pager", "查看服务最近日志", "定位服务失败原因"))
        return ("检测到 systemd 服务异常迹象。", ["确认失败服务", "检查服务日志和依赖", "人工评估后决定是否重启"], ["确认服务状态恢复", "复查最近错误日志"], commands)
    return (
        "检测到系统负载或进程异常线索，需要先完成只读采样。",
        ["确认当前负载趋势", "定位 CPU 和内存占用进程", "核对服务及业务影响"],
        ["重复采样并确认指标下降", "确认关键服务仍正常"],
        [("uptime", "查看负载与运行时间", "确认负载是否持续异常"),
         ("ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -n 15", "定位高 CPU 进程", "核对异常进程归属")],
    )


def _local_reply(question: str, context: str, history: list[dict[str, str]]) -> str:
    diagnosis, plan, validations, _ = _local_analysis(question.strip() or context)
    lines = [diagnosis, "", "建议步骤:"]
    lines.extend(f"{index}. {item}" for index, item in enumerate(plan, 1))
    if context.strip():
        lines.extend(["", "已将当前终端输出作为脱敏证据参考。"])
    if history:
        lines.append("我会继续基于本轮对话上下文回答；AI 仅只读分析，处理操作由管理员独立执行。")
    lines.extend(["", "验证:", *[f"- {item}" for item in validations]])
    return "\n".join(lines)


def chat_reply(settings: TerminalAIConfig, question: str, context: str, history: list[dict[str, str]]) -> tuple[str, str]:
    redacted_context = redact_context(context)
    redacted_question = redact_context(question)
    base_url = settings.base_url.strip().rstrip("/")
    model = settings.model.strip()
    if not settings.enabled or not base_url or not model:
        return _local_reply(redacted_question, redacted_context, history), "local-rules（未配置真实模型）"
    messages: list[dict[str, str]] = [{
        "role": "system",
        "content": "你是 Linux 智能综合运维平台的严格只读运维助手。用户问题优先于终端输出，终端输出只作为脱敏证据。你可以解答 Linux 和 Kubernetes 运维问题、定位故障并提供人工处理建议和验证步骤。你没有任何删除、修改、安装、部署或命令执行权限；不能申请权限、调用工具或将建议下发到 SSH/Kubernetes/Docker。写操作仅作为说明清楚风险的人工建议，由管理员独立确认和执行。不要泄露凭据、密钥或令牌；不得声称已检查未提供的证据、已执行、已恢复或已通知管理员。",
    }]
    messages.extend({"role": item["role"], "content": redact_context(item["content"])} for item in history[-20:])
    messages.append({"role": "user", "content": f"用户问题：{redacted_question.strip()}\n\n脱敏终端证据：\n{redacted_context}"})
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2}, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    key = settings.api_key
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = Request(f"{base_url}/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=settings.timeout_seconds) as response:
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise TerminalAssistantError("运维模型响应过大")
            payload: Any = json.loads(content.decode("utf-8"))
    except (HTTPError, URLError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TerminalAssistantError(f"运维模型请求失败：{type(exc).__name__}") from exc
    try:
        reply = payload["choices"][0]["message"]["content"]
        if not isinstance(reply, str):
            raise TypeError("Non-text model response")
        reply = reply.strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise TerminalAssistantError("运维模型响应格式无效") from exc
    if not reply:
        raise TerminalAssistantError("运维模型返回空回答")
    if len(reply) > 4000:
        raise TerminalAssistantError("运维模型回答超过对话长度限制，请缩短问题")
    return reply, f"openai-compatible:{model}"


def local_fallback_reply(question: str, context: str, history: list[dict[str, str]]) -> tuple[str, str]:
    """Return a deterministic read-only answer when an external model is unavailable."""
    return _local_reply(redact_context(question), redact_context(context), history), "local-rules（外部模型不可用，已降级）"
