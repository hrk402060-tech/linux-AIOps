import importlib.util
from pathlib import Path


def _release_module():
    path = Path(__file__).parents[2] / ".artifacts" / "release_mysql_hardening.py"
    spec = importlib.util.spec_from_file_location("release_mysql_hardening", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cmdb_release_manifest_is_scoped_to_snapshot_and_ingest() -> None:
    module = _release_module()
    assert module.CMDB_BACKEND == (
        "backend/src/services/cmdb_context.py",
        "backend/src/services/alerts.py",
    )
    assert set(module.CMDB_BACKEND).issubset(module.BACKEND)
    assert {
        "backend/tests/test_cmdb_context.py",
        "backend/tests/test_cmdb_context_ingest.py",
        "backend/tests/test_cmdb_release_manifest.py",
        "backend/tests/test_alerts_api.py",
    }.issubset(module.CMDB_TESTS)
