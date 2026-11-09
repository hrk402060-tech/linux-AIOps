from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.models.knowledge import KnowledgeAdvisory, KnowledgeSource, KnowledgeSyncRun
from src.schemas.knowledge import KnowledgeCoverageResponse, KnowledgeCoverageSourceResponse
from src.services import knowledge


def get_coverage(session: Session) -> KnowledgeCoverageResponse:
    sources = knowledge.list_sources(session)
    advisories = knowledge.list_advisories(session)
    runs = knowledge.list_sync_runs(session)
    return KnowledgeCoverageResponse(
        completeness_note="当前知识库仅覆盖已成功发布的数据源，不代表所有 Linux 漏洞。",
        publication_policy="stage_validate_publish",
        failure_policy="retain_active_release_and_alert",
        critical_check_interval_hours=4,
        sources=[_coverage_row(source, advisories, runs) for source in sources],
    )


def _coverage_row(
    source: KnowledgeSource,
    advisories: list[KnowledgeAdvisory],
    runs: list[KnowledgeSyncRun],
) -> KnowledgeCoverageSourceResponse:
    matched = [item for item in advisories if item.source_name == source.name]
    matched_runs = [item for item in runs if item.source_name == source.name]
    last_success = next((item for item in matched_runs if item.status == "completed"), None)
    latest_run = matched_runs[0] if matched_runs else None
    online = source.sync_mode in {"online_secondary", "vendor_official_api"}
    stale_hours = 48 if online else 720
    success_at = last_success.finished_at if last_success else source.last_sync_at
    is_stale = success_at is None or _as_utc(success_at) < (
        datetime.now(timezone.utc) - timedelta(hours=stale_hours)
    )
    health = _coverage_health(latest_run, success_at, is_stale)
    return KnowledgeCoverageSourceResponse(
        source_id=source.id, source_name=source.name, provider=source.provider,
        os_families=sorted({item.os_family for item in matched}), sync_mode=source.sync_mode,
        advisory_count=len(matched),
        latest_advisory_at=max((item.updated_at for item in matched), default=None),
        last_success_at=success_at, latest_sync_status=latest_run.status if latest_run else None,
        health=health, is_stale=is_stale, has_alert=health != "healthy",
        stale_after_hours=stale_hours, daily_schedule=source.schedule,
        critical_check_interval_hours=4 if online else None,
    )


def _coverage_health(
    latest_run: KnowledgeSyncRun | None, success_at: datetime | None, is_stale: bool
) -> str:
    if latest_run is not None and latest_run.status == "failed":
        return "failed"
    if success_at is None:
        return "never_synced"
    return "stale" if is_stale else "healthy"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
