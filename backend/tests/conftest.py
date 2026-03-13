from __future__ import annotations

import os
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.json"))
    return Path(os.environ["STORAGE_ROOT"])


@pytest.fixture
def client(storage_root: Path) -> TestClient:
    import app.config
    import app.services.review_service
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.services.review_service)
    importlib.reload(app.main)

    return TestClient(app.main.create_application())
