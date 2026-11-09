from dataclasses import dataclass
from typing import Protocol

from src.models.vulnerability import VulnerabilityFinding


@dataclass(frozen=True)
class TriageResult:
    priority: str
    risk_analysis: str
    remediation_steps: list[str]
    validation_steps: list[str]
    rollback_plan: str


class TriageProvider(Protocol):
    id: str
    name: str

    def analyze(self, finding: VulnerabilityFinding) -> TriageResult: ...


class LocalRulesProvider:
    id = "local-rules"
    name = "本地研判引擎"

    def analyze(self, finding: VulnerabilityFinding) -> TriageResult:
        manual = finding.conclusion == "manual_confirmation"
        priority = _priority(finding.severity, manual)
        certainty = (
            "当前证据无法可靠推断厂商 backport 状态，处置前必须人工核验公告。"
            if manual else "厂商安全公告与扫描证据一致，可进入人工修复评估。"
        )
        return TriageResult(
            priority=priority,
            risk_analysis=f"{finding.severity.upper()} 级漏洞影响 {finding.asset_name}。{certainty}",
            remediation_steps=[
                f"核对 {finding.advisory_id} 及当前系统适用范围",
                f"在维护窗口将 {finding.package_name} 更新至厂商修复版本 {finding.fixed_version}",
                "通过既有变更系统提交方案并由授权人员实施",
            ],
            validation_steps=[
                f"确认 {finding.package_name} 已运行厂商修复版本",
                "重新执行只读漏洞扫描并核验该证据链",
                "检查相关服务健康状态与业务监控",
            ],
            rollback_plan="实施前由变更负责人准备厂商支持的版本回退或快照恢复方案。",
        )


def provider_catalog() -> list[dict[str, object]]:
    return [{
        "id": LocalRulesProvider.id, "name": LocalRulesProvider.name,
        "kind": "local", "status": "ready", "requires_api_key": False,
    }, {
        "id": "openai", "name": "OpenAI", "kind": "external",
        "status": "disabled", "requires_api_key": True,
    }, {
        "id": "deepseek", "name": "DeepSeek", "kind": "external",
        "status": "disabled", "requires_api_key": True,
    }, {
        "id": "qwen", "name": "通义千问", "kind": "external",
        "status": "disabled", "requires_api_key": True,
    }]


def provider_metadata(provider_id: str) -> dict[str, object] | None:
    return next((item for item in provider_catalog() if item["id"] == provider_id), None)


def get_provider(provider_id: str) -> TriageProvider:
    if provider_id != LocalRulesProvider.id:
        raise ValueError("不支持的模型提供商")
    return LocalRulesProvider()


def _priority(severity: str, manual: bool) -> str:
    if manual:
        return "review"
    if severity == "critical":
        return "urgent"
    if severity == "high":
        return "high"
    return "normal"
