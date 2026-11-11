import re

from src.services.knowledge import KnowledgeImportError

VERSION = re.compile(r"^\d+(?:\.\d+){0,3}$")


def version_key(value: str) -> tuple[int, ...]:
    if not VERSION.fullmatch(value):
        raise KnowledgeImportError("上游公告包含不支持的版本格式")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def parse_nginx_ranges(value: str) -> list[dict[str, str]]:
    if value == "all":
        return [{"introduced": "0.0.0"}]
    result: list[dict[str, str]] = []
    for part in value.split(","):
        bounds = part.strip().split("-")
        if len(bounds) == 2:
            lower, upper = bounds
        elif len(bounds) == 1 and part.strip().endswith("+"):
            lower, upper = part.strip()[:-1], ""
        elif len(bounds) == 1 and VERSION.fullmatch(part.strip()):
            lower, upper = part.strip(), part.strip()
        else:
            raise KnowledgeImportError("nginx 官方公告版本范围不受支持")
        version_key(lower)
        item = {"introduced": lower}
        if upper:
            if version_key(upper) < version_key(lower):
                raise KnowledgeImportError("nginx 官方公告版本范围倒置")
            item["last_affected"] = upper
        result.append(item)
    return result


def affected_version(
    version: str, ranges: list[dict[str, object]], fixed_versions: list[str],
) -> bool:
    current = version_key(version)
    # nginx publishes maintained stable/mainline fixes independently. A wide
    # vulnerable range must not re-flag a fixed stable branch.
    for fixed in fixed_versions:
        threshold = version_key(fixed)
        if current[:2] == threshold[:2] and current >= threshold:
            return False
    for item in ranges:
        lower = version_key(str(item["introduced"]))
        fixed = version_key(str(item["fixed"])) if item.get("fixed") else None
        last = version_key(str(item["last_affected"])) if item.get("last_affected") else None
        if current >= lower and (fixed is None or current < fixed) and (
            last is None or current <= last
        ):
            return True
    return False


def installed_upstream_version(value: str) -> str | None:
    match = re.fullmatch(r"(?:\d+:)?(\d+(?:\.\d+){1,3})(?:[-+~][A-Za-z0-9.+:~_-]+)?", value)
    return match.group(1) if match else None
