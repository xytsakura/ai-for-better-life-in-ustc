from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from .config import Settings


DISCOVERY_TIMEOUT_SECONDS = 10.0
DISCOVERY_MAX_BYTES = 1024 * 1024
DISCOVERY_MAX_MODELS = 200
CATALOG_TTL_SECONDS = 600.0
SUPPORTED_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str
    chat_eligible: bool
    supported_reasoning_efforts: list[str]
    disabled_reason: str | None = None
    context_window_tokens: int | None = None
    context_window_source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelCatalogResult:
    models: list[ModelInfo]
    discovery_source: str | None
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "models": [model.as_dict() for model in self.models],
            "discovery_source": self.discovery_source,
            "cached": self.cached,
        }


@dataclass(frozen=True)
class UsageSummary:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    context_window_tokens: int | None = None
    context_usage_percent: float | None = None
    context_window_source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelCatalogError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


_CACHE: dict[tuple[str, str, int], tuple[float, ModelCatalogResult]] = {}


def invalidate_model_catalog() -> None:
    _CACHE.clear()


def normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip()
    if not value:
        raise ModelCatalogError("llm_base_url_required", "请先保存模型服务 Base URL")
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ModelCatalogError("invalid_llm_base_url", "模型服务 Base URL 必须包含协议和主机")
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, f"{host}{port}", path, "", "", ""))


def validate_base_url_for_saved_config(settings: Settings, base_url: str) -> str:
    normalized = normalize_base_url(base_url)
    parsed = urlparse(normalized)
    is_local_dev = _is_local_dev_url(parsed)
    if parsed.scheme != "https":
        if not is_local_dev or not settings.llm_allow_local_base_urls:
            raise ModelCatalogError(
                "unsafe_llm_base_url",
                "模型服务 Base URL 必须使用 https；本地开发地址需要显式开启白名单",
            )
    if not is_local_dev:
        _validate_resolved_addresses(parsed.hostname or "")
    return normalized


def default_model_info(model_id: str) -> ModelInfo:
    return classify_model(model_id)


def classify_model(model_id: str) -> ModelInfo:
    normalized = str(model_id or "").strip()
    lowered = normalized.lower()
    display_name = normalized
    if not normalized:
        return ModelInfo("", "", False, [], "empty_model_id")

    if "image" in lowered:
        return ModelInfo(normalized, display_name, False, [], "image_model_not_supported")
    if "audio" in lowered:
        return ModelInfo(normalized, display_name, False, [], "audio_model_not_supported")
    if "realtime" in lowered:
        return ModelInfo(normalized, display_name, False, [], "realtime_model_not_supported")
    if lowered == "codex-auto-review":
        return ModelInfo(normalized, display_name, False, [], "specialized_review_model")

    context_window = context_window_for_model(normalized)
    efforts: list[str] = []
    if _is_gpt_5_family(lowered):
        efforts = list(SUPPORTED_REASONING_EFFORTS)

    if _is_registered_text_model(lowered):
        return ModelInfo(
            normalized,
            display_name,
            True,
            efforts,
            None,
            context_window,
            "registry" if context_window else None,
        )

    return ModelInfo(
        normalized,
        display_name,
        False,
        [],
        "unknown_model_capability",
        context_window,
        "registry" if context_window else None,
    )


def context_window_for_model(model_id: str) -> int | None:
    lowered = model_id.lower()
    if lowered.startswith(("gpt-5.6", "gpt-5.5", "gpt-5.4")):
        return 272_000
    return None


def normalize_usage(raw_usage: Any, model: str) -> UsageSummary | None:
    if not isinstance(raw_usage, dict):
        return None
    input_tokens = _optional_int(raw_usage.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _optional_int(raw_usage.get("prompt_tokens"))
    output_tokens = _optional_int(raw_usage.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _optional_int(raw_usage.get("completion_tokens"))
    total_tokens = _optional_int(raw_usage.get("total_tokens"))
    input_details = raw_usage.get("input_tokens_details")
    output_details = raw_usage.get("output_tokens_details")
    cached_tokens = (
        _optional_int(input_details.get("cached_tokens"))
        if isinstance(input_details, dict)
        else None
    )
    cache_write_tokens = (
        _optional_int(input_details.get("cache_write_tokens"))
        if isinstance(input_details, dict)
        else None
    )
    reasoning_tokens = (
        _optional_int(output_details.get("reasoning_tokens"))
        if isinstance(output_details, dict)
        else None
    )
    context_window = context_window_for_model(model)
    context_percent: float | None = None
    if context_window and input_tokens is not None:
        context_percent = round(min(100.0, input_tokens / context_window * 100), 2)
    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        total_tokens=total_tokens,
        context_window_tokens=context_window,
        context_usage_percent=context_percent,
        context_window_source="registry" if context_window else None,
    )


class ModelCatalog:
    def __init__(self, settings: Settings):
        self.settings = settings

    def discover(self, force: bool = False) -> ModelCatalogResult:
        if not self.settings.llm_api_key:
            raise ModelCatalogError("llm_api_key_required", "请先保存模型服务 API key")
        normalized_base = validate_base_url_for_saved_config(
            self.settings, self.settings.llm_base_url
        )
        cache_key = self._cache_key(normalized_base)
        now = time.monotonic()
        if not force:
            cached = _CACHE.get(cache_key)
            if cached and now - cached[0] < CATALOG_TTL_SECONDS:
                return ModelCatalogResult(
                    models=cached[1].models,
                    discovery_source=cached[1].discovery_source,
                    cached=True,
                )

        last_error: ModelCatalogError | None = None
        for url in _candidate_model_urls(normalized_base):
            try:
                result = self._fetch_models(url)
            except ModelCatalogError as exc:
                last_error = exc
                continue
            _CACHE[cache_key] = (now, result)
            return result

        if last_error:
            raise last_error
        raise ModelCatalogError("model_discovery_failed", "未能发现可用模型", True)

    def get_cached(self) -> ModelCatalogResult | None:
        try:
            normalized_base = normalize_base_url(self.settings.llm_base_url)
        except ModelCatalogError:
            return None
        cached = _CACHE.get(self._cache_key(normalized_base))
        if not cached:
            return None
        if time.monotonic() - cached[0] >= CATALOG_TTL_SECONDS:
            return None
        return ModelCatalogResult(
            models=cached[1].models,
            discovery_source=cached[1].discovery_source,
            cached=True,
        )

    def model_for_query(self, requested_model: str | None) -> ModelInfo:
        selected = (requested_model or self.settings.llm_model or "").strip()
        if not selected:
            raise ModelCatalogError("model_required", "请选择模型")
        if selected == self.settings.llm_model:
            info = default_model_info(selected)
            if info.chat_eligible or info.disabled_reason == "unknown_model_capability":
                return info
            raise ModelCatalogError(
                info.disabled_reason or "model_not_available",
                "默认模型不适用于文本对话",
            )

        catalog = self.get_cached()
        if catalog is None:
            try:
                catalog = self.discover(force=False)
            except ModelCatalogError as exc:
                raise ModelCatalogError(
                    "model_catalog_unavailable",
                    "模型目录不可用，无法使用非默认模型",
                    exc.retryable,
                ) from exc
        for info in catalog.models:
            if info.id == selected:
                if info.chat_eligible:
                    return info
                raise ModelCatalogError(
                    info.disabled_reason or "model_not_available",
                    "所选模型不适用于文本对话",
                )
        raise ModelCatalogError("model_not_available", "所选模型不在当前模型目录中")

    def validate_reasoning(self, model: ModelInfo, effort: str | None) -> None:
        if effort is None:
            return
        if effort not in SUPPORTED_REASONING_EFFORTS:
            raise ModelCatalogError("invalid_reasoning_effort", "思考强度无效")
        if effort not in model.supported_reasoning_efforts:
            allowed = "、".join(model.supported_reasoning_efforts) or "无"
            raise ModelCatalogError(
                "reasoning_effort_not_supported",
                f"当前模型不支持该思考强度；允许值：{allowed}",
            )

    def _cache_key(self, normalized_base_url: str) -> tuple[str, str, int]:
        fingerprint = hashlib.sha256(
            self.settings.llm_api_key.encode("utf-8")
        ).hexdigest()
        return (
            normalized_base_url,
            fingerprint,
            int(getattr(self.settings, "llm_config_generation", 0)),
        )

    def _fetch_models(self, url: str) -> ModelCatalogResult:
        with httpx.Client(
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = client.get(
                url,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            )
        if 300 <= response.status_code < 400:
            raise ModelCatalogError("model_discovery_redirect", "模型发现不允许重定向", True)
        if response.status_code >= 400:
            raise ModelCatalogError(
                f"model_discovery_http_{response.status_code}",
                "模型发现请求失败",
                response.status_code in {429, 502, 503, 504},
            )
        if len(response.content) > DISCOVERY_MAX_BYTES:
            raise ModelCatalogError("model_discovery_response_too_large", "模型列表响应过大")
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelCatalogError("model_discovery_non_json", "模型列表不是 JSON") from exc
        ids = _extract_model_ids(data)
        if not ids:
            raise ModelCatalogError("model_discovery_empty", "模型列表为空或格式不支持")
        models = [classify_model(model_id) for model_id in ids[:DISCOVERY_MAX_MODELS]]
        return ModelCatalogResult(models=models, discovery_source=urlparse(url).path)


def _candidate_model_urls(normalized_base_url: str) -> list[str]:
    parsed = urlparse(normalized_base_url)
    base_models = normalized_base_url.rstrip("/") + "/models"
    origin_v1_models = urlunparse((parsed.scheme, parsed.netloc, "/v1/models", "", "", ""))
    candidates: list[str] = []
    for item in (base_models, origin_v1_models):
        if item not in candidates:
            candidates.append(item)
    return candidates


def _extract_model_ids(data: Any) -> list[str]:
    raw_items: Any
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            raw_items = data["data"]
        elif isinstance(data.get("models"), list):
            raw_items = data["models"]
        else:
            return []
    elif isinstance(data, list):
        raw_items = data
    else:
        return []

    seen: set[str] = set()
    ids: list[str] = []
    for item in raw_items:
        model_id: str | None = None
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            candidate = item.get("id") or item.get("name") or item.get("model")
            if isinstance(candidate, str):
                model_id = candidate
        if not model_id:
            continue
        clean = model_id.strip()
        if clean and clean not in seen:
            seen.add(clean)
            ids.append(clean)
        if len(ids) >= DISCOVERY_MAX_MODELS:
            break
    return ids


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _is_registered_text_model(model_id: str) -> bool:
    return _is_gpt_5_family(model_id) or model_id in {
        "codex-mini-latest",
        "codex-latest",
    }


def _is_gpt_5_family(model_id: str) -> bool:
    return model_id.startswith(("gpt-5.6", "gpt-5.5", "gpt-5.4"))


def _is_local_dev_url(parsed: Any) -> bool:
    host = (parsed.hostname or "").lower()
    return host in {"localhost"} or host.startswith("127.") or host == "::1"


def _validate_resolved_addresses(hostname: str) -> None:
    if hostname.lower() in {"localhost"}:
        addresses = ["127.0.0.1"]
    else:
        try:
            addresses = [
                item[4][0]
                for item in socket.getaddrinfo(hostname, None)
                if item and item[4]
            ]
        except socket.gaierror as exc:
            raise ModelCatalogError("llm_base_url_dns_failed", "模型服务主机无法解析") from exc
    if not addresses:
        raise ModelCatalogError("llm_base_url_dns_failed", "模型服务主机无法解析")
    for address in set(addresses):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ModelCatalogError("llm_base_url_dns_failed", "模型服务主机解析结果无效") from exc
        if _is_blocked_ip(ip):
            raise ModelCatalogError("unsafe_llm_base_url", "模型服务 Base URL 解析到受限网络地址")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
        return True
    if ip.is_reserved or ip.is_unspecified:
        return True
    if str(ip) == "169.254.169.254":
        return True
    return False
