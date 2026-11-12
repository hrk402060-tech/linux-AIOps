from collections import Counter


def apply_health(
    row: dict[str, object], evidence: dict[str, object], scoped: bool,
) -> dict[str, object]:
    probes = evidence.get("probes", [])
    counts = Counter(item["status"] for item in probes)  # type: ignore[union-attr]
    covered = len({item["api_id"] for item in probes  # type: ignore[union-attr]
                   if item["status"] != "disabled" and item["api_id"]})
    summary = {
        "configured": len(probes), "active": len(probes) - counts["disabled"],  # type: ignore[arg-type]
        **{key: counts[key] for key in ("disabled", "healthy", "failed", "unobserved", "stale")},
        "api_count": int(evidence.get("api_count", 0)), "covered_api_count": covered,
    }
    reasons = _reasons(row, summary, scoped)
    runtime = str(row["status"])
    if runtime == "unavailable":
        health = "unavailable"
    elif runtime == "degraded" or summary["failed"] or row["active_alerts"]:
        health = "degraded"
    elif runtime == "healthy" and not reasons:
        health = "healthy"
    else:
        health = "unobserved"
    return {
        **row, "runtime_status": runtime, "status": health, "probes": probes,
        "probe_summary": summary, "health_reasons": reasons,
        "health_scope": "authorized" if scoped else "all", "scope_complete": not scoped,
    }


def _reasons(
    row: dict[str, object], summary: dict[str, int], scoped: bool,
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    checks = [
        (scoped, "partial_scope", "仅展示授权范围，无法判断整个服务是否健康"),
        (row["asset_count"] == row["disabled_assets"], "no_enabled_assets", "尚未关联启用的服务器"),
        (row["offline_assets"], "hosts_offline", "存在近期连接失败的服务器"),
        (row["stale_assets"], "runtime_stale", "服务器采集证据已过期或时间异常"),
        (row["uncollected_assets"], "runtime_missing", "部分服务器尚无有效采集结果"),
        (row["active_alerts"], "active_asset_alerts", "关联资产存在活动告警，需排查其对服务的影响"),
        (not summary["active"], "probes_unconfigured", "尚未配置并启用服务或 API 拨测"),
        (summary["api_count"] > summary["covered_api_count"], "api_coverage_incomplete", "部分 API 尚未被启用的拨测覆盖"),
        (summary["failed"], "probes_failed", "近期拨测失败，请查看对应拨测证据"),
        (summary["unobserved"], "probes_unobserved", "拨测尚未执行或证据与当前配置不一致"),
        (summary["stale"], "probes_stale", "拨测证据已过期或时间异常"),
    ]
    for condition, code, message in checks:
        if condition:
            reasons.append({"code": code, "message": message})
    return reasons
