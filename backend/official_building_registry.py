"""Server-only MOLIT Building HUB enrichment for a verified VWorld building click."""

from __future__ import annotations

import os
import re
import logging
from typing import Any, Dict, Iterable, List, Optional
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
_PNU_PLAT_TO_HUB_PLAT = {"1": "0", "2": "1", "3": "2"}


class OfficialBuildingRegistryError(RuntimeError):
    """A structured upstream failure that never includes a service key."""


def _building_hub_upstream_status_reason(status_code: int) -> str:
    """Expose a safe HTTP classification without returning an upstream body."""

    status = int(status_code)
    if 100 <= status <= 599:
        return f"molit_building_hub_upstream_http_{status}"
    return "molit_building_hub_upstream_error"


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


def _building_hub_gateway_reason(body: str) -> Optional[str]:
    """Classify public Data.go gateway codes without retaining response content."""

    text = str(body or "")[:8192].upper()
    return next(
        (reason for marker, reason in _BUILDING_HUB_GATEWAY_REASONS.items() if marker in text),
        None,
    )


def _building_hub_failure_reason(status_code: int, body: str) -> str:
    return _building_hub_gateway_reason(body) or _building_hub_upstream_status_reason(status_code)


def service_key_configured() -> bool:
    return bool(_resolve_service_key())


def _normalize_service_key(value: str) -> Optional[str]:
    """Accept either Data.go's Encoding or Decoding service-key form."""

    candidate = value.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return None
    if re.search(r"%(?![0-9A-Fa-f]{2})", candidate):
        return None
    normalized = unquote(candidate)
    return normalized if normalized and not any(char.isspace() for char in normalized) else None


def _resolve_service_key() -> Optional[str]:
    semantic_aliases = sorted(
        env_key
        for env_key in os.environ
        if env_key not in BUILDING_HUB_ENV_KEYS
        and _SEMANTIC_BUILDING_HUB_ENV_KEY.fullmatch(env_key)
    )
    for env_key in (*BUILDING_HUB_ENV_KEYS, *semantic_aliases):
        value = (os.getenv(env_key) or "").strip()
        if value:
            normalized = _normalize_service_key(value)
            if normalized:
                return normalized
    return None


def building_hub_query_from_management_number(value: Any) -> Optional[Dict[str, str]]:
    """Convert BD_MGT_SN (PNU 19 digits plus a six digit serial) to HUB input."""

    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) != 25:
        return None

    pnu = digits[:19]
    plat_gb_cd = _PNU_PLAT_TO_HUB_PLAT.get(pnu[10])
    if not plat_gb_cd:
        return None

    return {
        "sigunguCd": pnu[:5],
        "bjdongCd": pnu[5:10],
        "platGbCd": plat_gb_cd,
        "bun": pnu[11:15],
        "ji": pnu[15:19],
    }


def _normalize_name(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


def _parse_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group(0)) if match else None


def _meaningful(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


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


def _selection_names(footprint: Dict[str, Any]) -> List[str]:
    verified = footprint.get("verified_properties")
    properties = verified if isinstance(verified, dict) else (
        footprint.get("properties") if isinstance(footprint.get("properties"), dict) else {}
    )
    raw_names = (
        None if isinstance(verified, dict) else footprint.get("display_name"),
        properties.get("buld_nm"),
        properties.get("buld_nm_dc"),
    )
    return [name for name in (_normalize_name(value) for value in raw_names) if name]


def _record_score(record: Dict[str, Any], selected_names: Iterable[str]) -> int:
    candidate_names = [
        _normalize_name(record.get("bldNm")),
        _normalize_name(record.get("dongNm")),
    ]
    score = 0
    for selected_name in selected_names:
        for candidate_name in candidate_names:
            if not candidate_name:
                continue
            if candidate_name == selected_name:
                score = max(score, 4)
            elif candidate_name in selected_name or selected_name in candidate_name:
                score = max(score, 2)
    return score


def _select_single_matching_record(records: List[Dict[str, Any],], footprint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if len(records) == 1:
        return records[0]
    selected_names = _selection_names(footprint)
    scored = [(_record_score(record, selected_names), index, record) for index, record in enumerate(records)]
    best_score = max((score for score, _, _ in scored), default=0)
    best_records = [record for score, _, record in scored if score == best_score]
    if best_score <= 0 or len(best_records) != 1:
        return None
    return best_records[0]


def _field_source(property_key: str, value: Any) -> Dict[str, Any]:
    return {
        "source": "molit_building_hub",
        "status": "official_verified",
        "property_key": property_key,
        "value": value,
    }


def _with_registry_unavailable(footprint: Dict[str, Any], reason: str) -> Dict[str, Any]:
    result = dict(footprint)
    result["registry_status"] = "unavailable"
    result["registry_reason"] = reason
    return result


async def enrich_verified_footprint(footprint: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge verified Building HUB fields without replacing an unverified click."""

    result = dict(footprint or {})
    if not (
        result.get("available")
        and result.get("official_footprint_available")
        and result.get("official_geometry_receipt")
        and result.get("official_selection_match")
    ):
        return _with_registry_unavailable(result, "official_geometry_not_verified")

    properties = dict(result.get("properties") or {})
    verified_properties = result.get("verified_properties")
    registry_identifiers = (
        verified_properties if isinstance(verified_properties, dict) else properties
    )
    footprint_receipt = result.get("official_footprint_receipt")
    if isinstance(footprint_receipt, dict):
        receipt_feature_id = str(footprint_receipt.get("native_feature_id") or "").strip()
        selected_feature_id = str(result.get("native_feature_id") or "").strip()
        receipt_management_id = str(footprint_receipt.get("bd_mgt_sn") or "").strip()
        selected_management_id = str(registry_identifiers.get("bd_mgt_sn") or "").strip()
        if (
            receipt_feature_id
            and selected_feature_id
            and receipt_feature_id != selected_feature_id
        ) or (
            receipt_management_id
            and selected_management_id
            and receipt_management_id != selected_management_id
        ):
            return _with_registry_unavailable(
                result,
                "official_geometry_identifier_mismatch",
            )
    query = building_hub_query_from_management_number(registry_identifiers.get("bd_mgt_sn"))
    if not query:
        return _with_registry_unavailable(result, "building_management_number_unavailable")

    service_key = _resolve_service_key()
    if not service_key:
        return _with_registry_unavailable(result, "molit_building_hub_key_not_configured")

    try:
        records = await _fetch_title_records(query, service_key)
    except OfficialBuildingRegistryError as error:
        return _with_registry_unavailable(result, str(error))

    if not records:
        return _with_registry_unavailable(result, "molit_building_hub_not_found")
    record = _select_single_matching_record(records, result)
    if not record:
        return _with_registry_unavailable(result, "official_registry_ambiguous")

    height_m = _parse_number(record.get("heit"))
    ground_floors = _parse_number(record.get("grndFlrCnt"))
    underground_floors = _parse_number(record.get("ugrndFlrCnt"))
    far_percent = _parse_number(record.get("vlRat"))
    bcr_percent = _parse_number(record.get("bcRat"))
    building_name = str(record.get("bldNm") or "").strip()
    building_name_detail = str(record.get("dongNm") or "").strip()
    building_use = str(record.get("mainPurpsCdNm") or "").strip()

    field_sources = dict(result.get("field_sources") or {})
    if height_m and height_m > 0:
        properties["buld_hg"] = round(height_m, 2)
        field_sources["height_m"] = _field_source("heit", round(height_m, 2))
    if ground_floors and ground_floors > 0:
        properties["gro_flo_co"] = int(round(ground_floors))
        field_sources["floor_count"] = _field_source("grndFlrCnt", int(round(ground_floors)))
    if underground_floors is not None and underground_floors >= 0:
        properties["und_flo_co"] = int(round(underground_floors))
    if far_percent is not None and far_percent >= 0:
        properties["far_percent"] = round(far_percent, 2)
        field_sources["far_percent"] = _field_source("vlRat", round(far_percent, 2))
    if bcr_percent is not None and bcr_percent >= 0:
        properties["bcr_percent"] = round(bcr_percent, 2)
        field_sources["bcr_percent"] = _field_source("bcRat", round(bcr_percent, 2))
    if building_name:
        properties["buld_nm"] = building_name
        field_sources["building_name"] = _field_source("bldNm", building_name)
    if building_name_detail:
        properties["buld_nm_dc"] = building_name_detail
    if building_use:
        properties["building_use"] = building_use
        properties["building_use_source"] = "molit_building_hub"

    chain = []
    for source in [*(result.get("source_chain") or []), "molit_building_hub"]:
        if source and source not in chain:
            chain.append(source)

    result["properties"] = properties
    result["field_sources"] = field_sources
    result["source_chain"] = chain
    result["display_name"] = building_name or building_name_detail or result.get("display_name")
    result["official_building_data"] = True
    result["registry_status"] = "official_verified"
    result["registry_reason"] = None
    result["registry_receipt"] = {
        "kind": "molit_building_hub_title",
        "query": query,
        "record_id": str(record.get("mgmBldrgstPk") or "").strip() or None,
        "bd_mgt_sn": str(registry_identifiers.get("bd_mgt_sn") or "").strip() or None,
        "native_feature_id": result.get("native_feature_id"),
        "record_count": len(records),
        "selection": "single_record" if len(records) == 1 else "building_name_match",
    }
    return result
