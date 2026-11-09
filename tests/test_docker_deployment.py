"""Contract checks for the portable Docker Compose deployment."""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DEPLOYMENT_FILES = (
    "compose.yaml",
    "backend/Dockerfile",
    "backend/entrypoint.sh",
    "frontend/Dockerfile",
    "frontend/nginx.conf",
    "deploy/.env.example",
    "docs/deployment.md",
    "scripts/verify_deployment.sh",
)


class DockerDeploymentTest(unittest.TestCase):
    """Verify the Linux migration and deployment contract."""

    def test_required_deployment_files_exist(self) -> None:
        for file_name in REQUIRED_DEPLOYMENT_FILES:
            with self.subTest(file_name=file_name):
                self.assertTrue((PROJECT_ROOT / file_name).is_file())

    def test_compose_defines_required_services_and_volume(self) -> None:
        compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
        for marker in ("postgres:", "backend:", "frontend:", "postgres_data:"):
            with self.subTest(marker=marker):
                self.assertIn(marker, compose)
        self.assertIn("healthcheck:", compose)

    def test_compose_does_not_contain_acceptance_secrets(self) -> None:
        compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertNotIn("sentinel-local-review-token", compose)
        self.assertNotIn("CREDENTIAL_MASTER_KEY=", compose)

    def test_backend_entrypoint_runs_migrations(self) -> None:
        entrypoint = (PROJECT_ROOT / "backend/entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("alembic -c alembic.ini upgrade head", entrypoint)

    def test_deployment_verifier_checks_security_boundaries(self) -> None:
        verifier = (PROJECT_ROOT / "scripts/verify_deployment.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("sed 's/\\r$//' deploy/.env", verifier)
        self.assertIn("unauthorized_status", verifier)
        self.assertIn("Plaintext secret found", verifier)
        self.assertIn("alembic_version", verifier)

    def test_deployment_verifier_checks_cmdb_and_incident_closure(self) -> None:
        verifier = (PROJECT_ROOT / "scripts/verify_deployment.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            "/api/v1/cmdb/businesses",
            "/api/v1/cmdb/applications",
            "/api/v1/cmdb/services",
            "/api/v1/cmdb/apis",
            "/api/v1/cmdb/tree",
            "/api/v1/audit-log?source=cmdb_change",
            "/api/v1/incidents",
            "/timeline",
            '[[ "$migration" == "20260918_24" ]]',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, verifier)


if __name__ == "__main__":
    unittest.main()
