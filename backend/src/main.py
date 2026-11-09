import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.auth import require_admin
from src.config import Settings, get_settings
from src.database import Base, create_session_factory
from src.routes.credentials import router as credentials_router
from src.routes.ai_agent import router as ai_agent_router
from src.routes.dashboard import router as dashboard_router
from src.routes.assets import router as assets_router
from src.routes.knowledge import router as knowledge_router
from src.routes.scan_tasks import router as scan_tasks_router
from src.routes.vulnerabilities import router as vulnerabilities_router
from src.routes.reports import router as reports_router
from src.routes.monitoring import router as monitoring_router
from src.routes.terminal import router as terminal_router
from src.routes.ops_assistant import router as ops_assistant_router
from src.routes.security_events import router as security_events_router
from src.routes.scan_schedules import router as scan_schedules_router
from src.routes.baselines import router as baselines_router
from src.routes.exposures import router as exposures_router
from src.routes.credential_risks import router as credential_risks_router
from src.routes.audit_log import router as audit_log_router
from src.routes.patches import router as patches_router
from src.routes.system import router as system_router
from src.routes.auth import router as auth_router
from src.routes.cmdb import router as cmdb_router
from src.routes.observability import router as observability_router
from src.routes.ssh_host_keys import router as ssh_host_keys_router
from src.routes.probes import router as probes_router
from src.routes.incidents import router as incidents_router
from src.routes.alerts import alert_router, connector_router, ingest_router
from src.routes.alert_notifications import router as alert_notifications_router
from src.routes.diagnostic_grants import router as diagnostic_grants_router
from src.routes.diagnostic_jobs import router as diagnostic_jobs_router
from src.routes.recovery import router as recovery_router
from src.routes.operations import router as operations_router
from src.models.ops_assistant import OpsConversation, OpsMessage  # noqa: F401
from src.services import diagnostic_job_state
from src.services.diagnostic_job_worker import (
    DiagnosticSchedulerHealth, run_scheduler as run_diagnostic_scheduler,
)
from src.schemas.auth import validate_password_policy
from src.services.auth import ensure_bootstrap_admin
from src.services.crypto import CredentialVault
from src.services import knowledge
from src.services import knowledge_backfill
from src.services.knowledge_sync import configure_sources, run_scheduler
from src.services.monitoring import run_scheduler as run_monitoring_scheduler
from src.services.terminal_connections import TerminalConnectionRegistry
from src.services.terminal_tickets import TerminalTicketRegistry
from src.services import terminal_sessions
from src.services.scan_schedules import run_scheduler as run_scan_schedule_scheduler
from src.services.alert_notification_delivery import run_scheduler as run_notification_scheduler

PROJECT_NAME = "app-linux-vuln-sentinel"


def get_project_metadata() -> dict[str, str]:
    """Expose the shared project metadata contract for root-level checks."""
    return {"name": PROJECT_NAME, "status": "design-review"}


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    session_factory = create_session_factory(runtime_settings.database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if runtime_settings.auto_create_schema:
            Base.metadata.create_all(session_factory.kw["bind"])
        with session_factory() as session:
            ensure_bootstrap_admin(
                session,
                runtime_settings.auth_bootstrap_username,
                runtime_settings.auth_bootstrap_password.get_secret_value(),
            )
            knowledge.ensure_sources(session)
            configure_sources(session, runtime_settings.connector_map)
            knowledge_backfill.recover_interrupted_jobs(session)
            terminal_sessions.recover_interrupted_sessions(session)
            if runtime_settings.auto_create_schema:
                diagnostic_job_state.seed(session)
        stop_event = asyncio.Event()
        scheduler = asyncio.create_task(run_scheduler(
            session_factory, runtime_settings.trusted_key_map,
            runtime_settings.connector_map, runtime_settings.knowledge_sync_interval_seconds,
            stop_event,
        ))
        monitoring_scheduler = asyncio.create_task(run_monitoring_scheduler(
            session_factory, application.state.credential_vault,
            runtime_settings.monitoring_interval_seconds, stop_event,
        ))
        scan_schedule_scheduler = asyncio.create_task(run_scan_schedule_scheduler(
            session_factory, application.state.credential_vault, stop_event,
        ))
        notification_scheduler = asyncio.create_task(run_notification_scheduler(
            session_factory, application.state.credential_vault, stop_event,
        ))
        diagnostic_scheduler = (asyncio.create_task(run_diagnostic_scheduler(
            session_factory, application.state.credential_vault, stop_event,
            application.state.diagnostic_scheduler_health,
        )) if runtime_settings.diagnostic_workers_enabled else None)
        try:
            yield
        finally:
            stop_event.set()
            if diagnostic_scheduler is not None:
                await diagnostic_scheduler
            await asyncio.gather(
                scheduler, monitoring_scheduler, scan_schedule_scheduler, notification_scheduler,
            )

    application = FastAPI(
        title="Linux Intelligent Integrated Operations Platform API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(security_events_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(diagnostic_jobs_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(recovery_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(operations_router, prefix='/api/v1')
    application.include_router(scan_schedules_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(baselines_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(exposures_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(credential_risks_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(audit_log_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(patches_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(system_router, prefix='/api/v1', dependencies=[Depends(require_admin)])
    application.include_router(auth_router, prefix="/api/v1")
    application.state.session_factory = session_factory
    application.state.api_admin_token = runtime_settings.api_admin_token.get_secret_value()
    application.state.runtime_settings = runtime_settings
    application.state.diagnostic_scheduler_health = DiagnosticSchedulerHealth(
        enabled=runtime_settings.diagnostic_workers_enabled,
    )
    if len(application.state.api_admin_token) < 32:
        raise ValueError("API_ADMIN_TOKEN must contain at least 32 characters")
    bootstrap_password = runtime_settings.auth_bootstrap_password.get_secret_value()
    if bool(runtime_settings.auth_bootstrap_username) != bool(bootstrap_password):
        raise ValueError("AUTH_BOOTSTRAP_USERNAME and AUTH_BOOTSTRAP_PASSWORD must be set together")
    if bootstrap_password:
        validate_password_policy(bootstrap_password)
    application.state.credential_vault = CredentialVault(
        runtime_settings.credential_master_key, runtime_settings.credential_key_version
    )
    application.state.knowledge_trusted_keys = runtime_settings.trusted_key_map
    application.state.knowledge_connectors = runtime_settings.connector_map
    application.state.allowed_origins = set(runtime_settings.origin_list)
    application.state.allowed_origin_regex = runtime_settings.origin_regex
    application.state.terminal_tickets = TerminalTicketRegistry(
        runtime_settings.terminal_ticket_ttl_seconds
    )
    application.state.terminal_connections = TerminalConnectionRegistry(
        runtime_settings.terminal_max_connections
    )
    application.state.terminal_idle_timeout = runtime_settings.terminal_idle_timeout_seconds
    application.state.terminal_max_duration = runtime_settings.terminal_max_duration_seconds
    application.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.origin_list,
        allow_origin_regex=runtime_settings.origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.include_router(
        credentials_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        assets_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        cmdb_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        observability_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        probes_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        incidents_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        connector_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        alert_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(ingest_router, prefix="/api/v1")
    application.include_router(
        diagnostic_grants_router, prefix='/api/v1', dependencies=[Depends(require_admin)]
    )
    application.include_router(
        alert_notifications_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        knowledge_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        scan_tasks_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        vulnerabilities_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        dashboard_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        ai_agent_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        reports_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(
        monitoring_router, prefix="/api/v1", dependencies=[Depends(require_admin)]
    )
    application.include_router(terminal_router, prefix="/api/v1")
    application.include_router(
        ops_assistant_router, prefix="/api/v1",
    )

    application.include_router(
        ssh_host_keys_router,
        prefix="/api/v1",
        dependencies=[Depends(require_admin)],
    )

    @application.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
