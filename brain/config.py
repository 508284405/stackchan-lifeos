"""Provider configuration loader — env/secret injection, no key in logs."""

from __future__ import annotations

import os

from .models import ProviderConfig


def provider_config_from_env(prefix: str = "SUB2API") -> ProviderConfig | None:
    base = os.environ.get(f"{prefix}_BASE_URL", "").strip()
    model = os.environ.get(f"{prefix}_MODEL", "").strip()
    key = os.environ.get(f"{prefix}_API_KEY", "")
    if not base and not model and not key:
        return None
    missing = [name for name, value in (("BASE_URL", base), ("MODEL", model), ("API_KEY", key.strip())) if not value]
    if missing:
        raise ValueError(f"missing required provider configuration: {', '.join(missing)}")
    return ProviderConfig(
        base_url=base,
        model=model,
        api_key=key,
        connect_timeout_seconds=float(os.environ.get(f"{prefix}_CONNECT_TIMEOUT_SECONDS", "5")),
        request_timeout_seconds=float(os.environ.get(f"{prefix}_REQUEST_TIMEOUT_SECONDS", "20")),
        tls_verify=os.environ.get(f"{prefix}_TLS_VERIFY", "true").lower() not in {"0", "false", "no"},
        allow_http_localhost=os.environ.get(f"{prefix}_ALLOW_HTTP_LOCALHOST", "false").lower() in {"1", "true", "yes"},
    )
