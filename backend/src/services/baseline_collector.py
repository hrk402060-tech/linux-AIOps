import shlex

import paramiko

from src.models.asset import Asset
from src.models.credential import Credential
from src.services import scan_engine

POLICY_ID = "linux-security-baseline"
POLICY_VERSION = "2026.09"
RULES = (
    ("ssh-root-login", "SSH 禁止 root 直接登录", "high", "permitrootlogin", {"no", "prohibit-password"}),
    ("ssh-password-auth", "SSH 禁止密码认证", "medium", "passwordauthentication", {"no"}),
    ("firewall-active", "主机防火墙已启用", "high", "firewall", {"active"}),
    ("time-sync", "时间同步服务正常", "medium", "timesync", {"active"}),
    ("audit-service", "审计服务正常", "high", "audit", {"active"}),
)


def collect(
    asset: Asset, credential: Credential, secret: str,
    expected_host_key: paramiko.PKey | None = None,
) -> list[dict[str, object]]:
    client = scan_engine._connect(asset, credential, secret, expected_host_key)
    try:
        values = _parse(scan_engine._execute(client, _command()))
    finally:
        client.close()
    return [
        {
            "rule_id": rule_id, "title": title, "severity": severity,
            "status": "passed" if values.get(key, "unknown") in accepted else "failed",
            "observed": values.get(key, "unknown"),
            "expected": " / ".join(sorted(accepted)),
            "evidence_source": "SSH 只读配置检查",
        }
        for rule_id, title, severity, key, accepted in RULES
    ]


def _command() -> str:
    script = """
sshd=$(sshd -T 2>/dev/null || true)
printf 'permitrootlogin=%s\n' "$(printf '%s\n' "$sshd" | awk '$1=="permitrootlogin"{print $2;exit}')"
printf 'passwordauthentication=%s\n' "$(printf '%s\n' "$sshd" | awk '$1=="passwordauthentication"{print $2;exit}')"
systemctl is-active firewalld nftables ufw 2>/dev/null | grep -q active && echo firewall=active || echo firewall=inactive
systemctl is-active chronyd systemd-timesyncd 2>/dev/null | grep -q active && echo timesync=active || echo timesync=inactive
systemctl is-active auditd 2>/dev/null | grep -q active && echo audit=active || echo audit=inactive
"""
    return "sh -c " + shlex.quote(script)


def _parse(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"permitrootlogin", "passwordauthentication", "firewall", "timesync", "audit"}:
            values[key] = value.strip().lower() or "unknown"
    return values
