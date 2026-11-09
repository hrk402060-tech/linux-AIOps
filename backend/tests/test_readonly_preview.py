import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_preview() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/preview_readonly_workflow.py"
    spec = importlib.util.spec_from_file_location("readonly_preview", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_refuses_nonisolated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_preview()
    from src.config import Settings

    monkeypatch.setattr(module, "Settings", lambda: Settings(database_url="sqlite:///production.db"))
    with pytest.raises(RuntimeError, match="isolated /preview"):
        module.main()


def test_preview_refuses_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_preview()
    from src.config import Settings

    monkeypatch.setattr(module, "Settings", lambda: Settings(database_url="sqlite:////preview/existing.db"))
    monkeypatch.setattr(module.Path, "exists", lambda _: True)
    with pytest.raises(RuntimeError, match="overwrite"):
        module.main()


def test_preview_refuses_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_preview()
    from src.config import Settings

    monkeypatch.setattr(module, "Settings", lambda: Settings(database_url="sqlite:////preview/../other.db"))
    with pytest.raises(RuntimeError, match="inside the isolated"):
        module.main()
