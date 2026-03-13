from __future__ import annotations

import ssl
from pathlib import Path

import httpx

from app.domain.models.runtime_settings import RuntimeSettings


class HttpClientFactory:
    @classmethod
    def create(
        cls,
        *,
        timeout: httpx.Timeout,
        runtime_settings: RuntimeSettings | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Client:
        return httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=cls.build_verify(runtime_settings),
        )

    @classmethod
    def build_verify(cls, runtime_settings: RuntimeSettings | None = None) -> bool | ssl.SSLContext:
        runtime = runtime_settings or RuntimeSettings()
        if not runtime.verify_ssl:
            return False

        cafile = str(runtime.ca_bundle_path or "").strip()
        if cafile:
            ca_path = Path(cafile)
            if not ca_path.exists():
                raise RuntimeError(f"Configured CA bundle path does not exist: {ca_path}")
            return ssl.create_default_context(cafile=str(ca_path))

        context = ssl.create_default_context()
        if runtime.use_system_trust_store:
            try:
                context.load_default_certs()
            except Exception:
                pass
        return context
