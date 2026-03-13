from __future__ import annotations

import ssl
from pathlib import Path

import httpx

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.http_client_factory import HttpClientFactory


def test_http_client_factory_disables_ssl_verification(monkeypatch):
    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", DummyClient)

    HttpClientFactory.create(
        timeout=httpx.Timeout(5.0),
        runtime_settings=RuntimeSettings(verify_ssl=False),
    )

    assert captured["verify"] is False


def test_http_client_factory_uses_ca_bundle(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}
    ca_bundle = tmp_path / "corp-ca.pem"
    ca_bundle.write_text("dummy", encoding="utf-8")
    sentinel_context = object()

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", DummyClient)
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: sentinel_context)

    HttpClientFactory.create(
        timeout=httpx.Timeout(5.0),
        runtime_settings=RuntimeSettings(ca_bundle_path=str(ca_bundle)),
    )

    assert captured["verify"] is sentinel_context


def test_http_client_factory_uses_system_trust_store(monkeypatch):
    captured: dict[str, object] = {}
    load_default_certs_called = {"value": False}

    class DummyContext:
        def load_default_certs(self) -> None:
            load_default_certs_called["value"] = True

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", DummyClient)
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: DummyContext())

    HttpClientFactory.create(
        timeout=httpx.Timeout(5.0),
        runtime_settings=RuntimeSettings(use_system_trust_store=True),
    )

    assert load_default_certs_called["value"] is True
    assert captured["verify"].__class__.__name__ == "DummyContext"
