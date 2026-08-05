from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, status

from .config import Settings


PRIVATE_HOSTS = {"localhost"}


def _host_is_private(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in PRIVATE_HOSTS or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _matches_prefix(url: str, prefixes: tuple[str, ...]) -> bool:
    normalized = url.rstrip("/")
    return any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in prefixes)


def validate_url_safety(url: str, trust_level: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_url")

    if trust_level == "first_party_internal" and _matches_prefix(url, settings.internal_url_allowlist):
        return

    if parsed.scheme != "https":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="url_requires_https")

    if _host_is_private(parsed.hostname):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="private_url_not_allowed")

    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="url_host_unresolvable") from exc
    if not addresses or any(_host_is_private(item[4][0]) for item in addresses):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="private_url_not_allowed")


def sanitize_public_manifest(source: dict) -> dict:
    manifest = dict(source)
    icon = manifest.get("icon")
    if isinstance(icon, str) and urlparse(icon).scheme in {"http", "https"}:
        manifest.pop("icon", None)
    integration = dict(manifest.get("integration", {}))
    for key in ("chat_endpoint", "health_endpoint", "callback_urls"):
        integration.pop(key, None)
    manifest["integration"] = integration
    return manifest


def ensure_no_public_endpoint_leak(record: dict) -> dict:
    """Return a public record without private endpoints or registry metadata."""
    record = dict(record)
    active = record.get("active_version")
    if isinstance(active, dict):
        record["active_version"] = {
            key: value
            for key, value in active.items()
            if key
            in {
                "version_id",
                "version",
                "review_status",
                "deployment_status",
                "created_at",
                "updated_at",
            }
        }
        record["active_version"]["manifest"] = sanitize_public_manifest(active.get("manifest", {}))
    health = record.get("latest_health")
    if isinstance(health, dict):
        record["latest_health"] = {key: value for key, value in health.items() if key != "safe_detail"}
    record.pop("previous_active_version_id", None)
    return record


def safe_error(code: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": code})
