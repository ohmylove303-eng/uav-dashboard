"""Server-only credential resolution and HTTP client for MOLIT Building HUB."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import httpx


LOGGER = logging.getLogger(__name__)

BUILDING_HUB_TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
BUILDING_HUB_ENV_KEYS = (
    "MOLIT_BUILDING_HUB_SERVICE_KEY",
    "MOLIT_BUILDING_HUB_API_KEY",
    "MOLIT_BUILDING_HUB_KEY",
    "MOLIT_BUILDING_REGISTRY_SERVICE_KEY",
    "MOLIT_BUILDING_REGISTRY_API_KEY",
    "MOLIT_BUILDING_REGISTRY_KEY",
    "MOLIT_BUILDING_SERVICE_KEY",
    "MOLIT_BUILDING_API_KEY",
    "DATA_GO_KR_BUILDING_HUB_SERVICE_KEY",
    "DATA_GO_KR_BUILDING_HUB_API_KEY",
    "BUILDING_HUB_SERVICE_KEY",
    "BUILDING_HUB_API_KEY",
    "BUILDING_HUB_KEY",
)
_SEMANTIC_BUILDING_HUB_ENV_KEY = re.compile(
    r"^(?:MOLIT|DATA_GO_KR)_[A-Z0-9_]*(?:BUILDING|BLDG|REGISTRY)[A-Z0-9_]*(?:SERVICE_)?KEY$"
)
BUILDING_HUB_TIMEOUT_S = float(os.getenv("MOLIT_BUILDING_HUB_TIMEOUT_S", "6.0"))
BUILDING_HUB_CREDENTIAL_FAILURES = {
    "molit_building_hub_access_denied",
    "molit_building_hub_key_expired",
    "molit_building_hub_key_missing",
    "molit_building_hub_key_unregistered",
    "molit_building_hub_upstream_http_401",
    "molit_building_hub_upstream_http_403",
}
_BUILDING_HUB_GATEWAY_REASONS = {
    "SERVICE_ACCESS_DENIED_ERROR": "molit_building_hub_access_denied",
    "PERMISSION_DENIED": "molit_building_hub_access_denied",
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": "molit_building_hub_key_unregistered",
    "DEADLINE_HAS_EXPIRED_ERROR": "molit_building_hub_key_expired",
    "SERVICE_KEY_IS_NULL": "molit_building_hub_key_missing",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": "molit_building_hub_quota_exceeded",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR": "molit_building_hub_rate_limited",
    "INVALID_REQUEST_PARAMETER_ERROR": "molit_building_hub_invalid_request",
    "NO_OPENAPI_SERVICE_ERROR": "molit_building_hub_service_unavailable",
}


class OfficialBuildingRegistryError(RuntimeError):
    """A structured upstream failure that never includes a service key."""


def _building_hub_upstream_status_reason(status_code: int) -> str:
    """Expose a safe HTTP classification without returning an upstream body."""

    status = int(status_code)
    if 100 <= status <= 599:
        return f"molit_building_hub_upstream_http_{status}"
    return "molit_building_hub_upstream_error"


def _building_hub_gateway_reason(body: str) -> Optional[str]:
    """Classify public Data.go gateway codes without retaining response content."""

    text = str(body or "")[:8192].upper()
    return next(
        (reason for marker, reason in _BUILDING_HUB_GATEWAY_REASONS.items() if marker in text),
        None,
    )


def _building_hub_failure_reason(status_code: int, body: str) -> str:
    return _building_hub_gateway_reason(body) or _building_hub_upstream_status_reason(status_code)


def _normalize_service_key(value: str) -> Optional[str]:
    """Accept either Data.go's Encoding or Decoding service-key form."""

    candidate = value.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return None
    if re.search(r"%(?![0-9A-Fa-f]{2})", candidate):
        return None
    normalized = unquote(candidate)
    return normalized if normalized and not any(char.isspace() for char in normalized) else None


def _service_key_candidates() -> List[Tuple[str, str]]:
    semantic_aliases = sorted(
        env_key
        for env_key in os.environ
        if env_key not in BUILDING_HUB_ENV_KEYS
        and _SEMANTIC_BUILDING_HUB_ENV_KEY.fullmatch(env_key)
    )
    candidates = []
    seen_values = set()
    for env_key in (*BUILDING_HUB_ENV_KEYS, *semantic_aliases):
        value = (os.getenv(env_key) or "").strip()
        if value:
            normalized = _normalize_service_key(value)
            if normalized and normalized not in seen_values:
                seen_values.add(normalized)
                candidates.append((env_key, normalized))
    return candidates


def _resolve_service_key() -> Optional[str]:
    candidates = _service_key_candidates()
    return candidates[0][1] if candidates else None


def service_key_configured() -> bool:
    return bool(_service_key_candidates())


def _as_records(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        raise OfficialBuildingRegistryError("molit_building_hub_invalid_response")

    response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    header = response.get("header") if isinstance(response.get("header"), dict) else {}
    result_code = str(header.get("resultCode") or header.get("resultCd") or "").strip()
    if result_code and result_code not in {"00", "0", "0000", "NORMAL_SERVICE"}:
        safe_reason = _building_hub_gateway_reason(
            f"{result_code} {header.get('resultMsg') or header.get('resultMessage') or ''}"
        )
        raise OfficialBuildingRegistryError(safe_reason or "molit_building_hub_rejected")

    body = response.get("body") if isinstance(response.get("body"), dict) else {}
    items = body.get("items") if isinstance(body.get("items"), dict) else body.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if items is None:
        items = body.get("item")
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


async def _fetch_title_records(query: Dict[str, str], service_key: str) -> List[Dict[str, Any]]:
    params = {
        "serviceKey": service_key,
        **query,
        "_type": "json",
        "numOfRows": "100",
        "pageNo": "1",
    }
    timeout = httpx.Timeout(BUILDING_HUB_TIMEOUT_S, connect=min(BUILDING_HUB_TIMEOUT_S, 3.0))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(BUILDING_HUB_TITLE_URL, params=params)
    except httpx.TimeoutException as error:
        raise OfficialBuildingRegistryError("molit_building_hub_timeout") from error
    except httpx.HTTPError as error:
        raise OfficialBuildingRegistryError("molit_building_hub_network_error") from error

    if response.status_code != 200:
        reason = _building_hub_failure_reason(response.status_code, response.text)
        LOGGER.warning(
            "official_provider_failure provider=molit_building_hub status=%s reason=%s",
            response.status_code,
            reason,
        )
        raise OfficialBuildingRegistryError(reason)
    try:
        payload = response.json()
    except ValueError as error:
        gateway_reason = _building_hub_gateway_reason(response.text)
        if gateway_reason:
            LOGGER.warning(
                "official_provider_failure provider=molit_building_hub status=%s reason=%s",
                response.status_code,
                gateway_reason,
            )
            raise OfficialBuildingRegistryError(gateway_reason) from error
        raise OfficialBuildingRegistryError("molit_building_hub_non_json_response") from error
    return _as_records(payload)
