import re

POLICY_VERSION = "2026.09"
COMMON_PASSWORDS = {
    "123456", "12345678", "admin", "password", "root", "qwerty",
    "admin123", "password123", "root123", "changeme",
}


def assess(secret: str, username: str) -> dict[str, object]:
    normalized = secret.casefold()
    violations: list[str] = []
    if len(secret) < 12:
        violations.append("minimum_length")
    if sum(bool(re.search(pattern, secret)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")) < 3:
        violations.append("character_variety")
    if normalized in COMMON_PASSWORDS:
        violations.append("common_password")
    if username.casefold() and username.casefold() in normalized:
        violations.append("contains_username")
    if re.search(r"(.)\1{3,}", secret):
        violations.append("repeated_characters")
    severity = "high" if {"common_password", "contains_username"} & set(violations) or len(secret) < 8 else "medium" if violations else "low"
    return {"risk_level": severity, "status": "at_risk" if violations else "passed", "violations": violations}
