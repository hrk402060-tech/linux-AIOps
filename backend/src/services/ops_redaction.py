import json
import re

from src.services.terminal_assistant import redact_context

SECRET_NAME = re.compile(
    r"password|passwd|token|secret|api[_-]?key|authorization", re.IGNORECASE,
)
QUOTED_SECRET = re.compile(
    r"""((?:["'])(?:[\w.-]*(?:password|passwd|token|secret|api[_-]?key|authorization)[\w.-]*)(?:["'])\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)""",
    re.IGNORECASE,
)
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.DOTALL,
)


def _redact_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_NAME.search(key) else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    value = PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
    value = QUOTED_SECRET.sub(lambda match: f'{match.group(1)}"[REDACTED]"', value)
    return redact_context(value)


def redact(value: str) -> str:
    if value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return _redact_text(value)
        try:
            return json.dumps(_redact_json(parsed), ensure_ascii=False)[:12000]
        except RecursionError:
            return "[REDACTED: 内容嵌套过深]"
    return _redact_text(value)
