from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.config import get_settings
from src.database import Base
from src.models.credential import Credential  # noqa: F401
from src.models.probes import HttpProbe, HttpProbeRun  # noqa: F401
from src.models.incident import Incident, IncidentTicket, IncidentTimeline  # noqa: F401
from src.models.alert import (  # noqa: F401
    Alert, AlertConnector, AlertTimeline, RootCauseAnalysis,
)
from src.models.alert_connector_audit import AlertConnectorAudit  # noqa: F401
from src.models.alert_ingest_receipt import AlertIngestReceipt  # noqa: F401
from src.models.alert_notification import (  # noqa: F401
    AlertNotification, AlertNotificationAudit, AlertNotificationConfig, AlertNotificationReceipt,
)
from src.models.asset import Asset  # noqa: F401
from src.models.knowledge import (  # noqa: F401
    KnowledgeAdvisory, KnowledgeBackfillJob, KnowledgeRelease, KnowledgeSource,
    KnowledgeSyncRun,
)
from src.models.scan_task import ScanSchedule, ScanTask  # noqa: F401
from src.models.scan_task_removal import ScanTaskRemoval  # noqa: F401
from src.models.vulnerability import (  # noqa: F401
    VulnerabilityCase, VulnerabilityDeletionAudit, VulnerabilityFinding,
)
from src.models.ai_agent import AgentRecommendation  # noqa: F401
from src.models.diagnostic_grant import DiagnosticGrant, DiagnosticGrantAudit  # noqa: F401
from src.models.diagnostic_job import DiagnosticAdmission, DiagnosticJob, DiagnosticSlot  # noqa: F401
from src.models.recovery import RecoveryEvaluation, RecoveryVerification  # noqa: F401
from src.models.report import ScanReport  # noqa: F401
from src.models.monitoring import (  # noqa: F401
    MonitoringAlertState, MonitoringEvent, MonitoringPolicy, MonitoringSnapshot,
)
from src.models.terminal import (  # noqa: F401
    TerminalAssistantAnalysis, TerminalCommandAudit, TerminalSession,
)
from src.models.data_lifecycle import AssetCleanupAudit  # noqa: F401
from src.models.security_event import EventDisposition  # noqa: F401
from src.models.baseline import BaselineTask  # noqa: F401
from src.models.exposure import ExposureTask  # noqa: F401
from src.models.credential_risk import CredentialRiskTask  # noqa: F401
from src.models.ai_provider import AIProviderAudit, AIProviderCredential  # noqa: F401
from src.models.cmdb import (  # noqa: F401
    CmdbApplication, CmdbBusiness, CmdbChangeAudit, CmdbService,
    CmdbServiceApi, CmdbServiceAsset,
)
from src.models.ssh_host_key import SshHostKey  # noqa: F401
from src.models.ssh_host_key_audit import SshHostKeyAudit  # noqa: F401
from src.models.ops_assistant import OpsConversation, OpsMessage  # noqa: F401
from src.models.platform_setting import PlatformSetting  # noqa: F401
from src.models.asset_access import UserAssetAccess, UserAccessAudit  # noqa: F401
from src.models.auth import User  # noqa: F401
from src.models.operations import AutomationApproval, ChangeRecord  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
