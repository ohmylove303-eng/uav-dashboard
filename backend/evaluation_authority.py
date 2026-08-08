"""Validation and binding for one authoritative evaluation snapshot."""

from datetime import datetime, timezone
from typing import Any, Dict, Final, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError


KMA_WEATHER_SOURCES: Final = frozenset({
    "kma_surface_observation",
    "kma_surface_forecast",
    "kma_surface_cache",
})
DECISION_WEATHER_FIELDS: Final = (
    "wind_speed",
    "gust_speed",
    "visibility",
    "precipitation_prob",
    "weather_code",
)


class WeatherEvidenceContext(BaseModel):
    """Trusted server context required to bind a weather receipt to a decision."""

    model_config = ConfigDict(frozen=True)

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    selection_id: Optional[str] = None
    selection_snapshot_id: Optional[str] = None
    require_selection_binding: bool = False


class AuthoritativeWeatherReceipt(BaseModel):
    """Parsed KMA receipt accepted by the flight-decision authority boundary."""

    model_config = ConfigDict(extra="allow", frozen=True)

    kind: Literal["kma_weather_observation"]
    receipt_id: StrictStr = Field(min_length=1)
    authority_source: StrictStr
    source: StrictStr
    source_chain: tuple[StrictStr, ...] = Field(min_length=1)
    stale_cache: Literal[False]
    selection_id: Optional[UUID] = None
    observed_at_utc: datetime
    expires_at_utc: datetime
    latitude: float
    longitude: float
    values: dict[StrictStr, Union[float, int]]


def _normalize_source_chain(*parts: Any) -> List[str]:
    chain: List[str] = []
    for part in parts:
        values = part if isinstance(part, (list, tuple, set)) else [part]
        for value in values:
            token = str(value or "").strip()
            if token and token not in chain:
                chain.append(token)
    return chain


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _receipt_values_match(weather: Dict[str, Any], receipt: AuthoritativeWeatherReceipt) -> bool:
    return all(field in receipt.values and receipt.values[field] == weather.get(field) for field in DECISION_WEATHER_FIELDS)


def build_weather_evidence(
    weather: Dict[str, Any],
    *,
    context: WeatherEvidenceContext,
    upper_air_available: bool = False,
    wind_profiler_available: bool = False,
) -> Dict[str, Any]:
    """Return decision authority only for a complete, fresh KMA receipt."""
    source_chain = _normalize_source_chain(weather.get("source_chain") or [], weather.get("source"))
    stale_cache = bool(weather.get("stale_cache", False))
    available = bool(weather.get("available", "weather_unavailable" not in source_chain))
    authority_source = weather.get("authority_source")
    receipt = dict(weather.get("receipt") or {}) if isinstance(weather.get("receipt"), dict) else {}
    reason = weather.get("reason")
    parsed_receipt: Optional[AuthoritativeWeatherReceipt] = None

    if not available:
        reason = reason or "authoritative_weather_missing"
    elif stale_cache:
        reason = "weather_cache_expired"
    elif weather.get("authoritative") is not True:
        reason = "authoritative_weather_untrusted"
    elif source_chain and not any(source in KMA_WEATHER_SOURCES for source in source_chain):
        reason = "authoritative_weather_untrusted"
    elif not authority_source:
        reason = "authoritative_weather_source_missing"
    elif authority_source not in KMA_WEATHER_SOURCES or authority_source not in source_chain:
        reason = "authoritative_weather_untrusted"
    elif not receipt:
        reason = "authoritative_weather_receipt_missing"
    else:
        receipt_selection_id = receipt.get("selection_id")
        if context.require_selection_binding and receipt_selection_id is None:
            reason = "weather_selection_id_missing"
        elif receipt_selection_id is not None and not isinstance(receipt_selection_id, str):
            reason = "weather_selection_id_invalid"
        elif isinstance(receipt_selection_id, str) and not _is_uuid(receipt_selection_id):
            reason = "weather_selection_id_invalid"
        elif context.require_selection_binding and not context.selection_snapshot_id:
            reason = "weather_selection_snapshot_missing"
        elif not receipt.get("observed_at_utc") or not receipt.get("expires_at_utc"):
            reason = "weather_freshness_missing"
        elif not receipt.get("source"):
            reason = "authoritative_weather_receipt_source_missing"
        elif not isinstance(receipt.get("source_chain"), list) or not receipt["source_chain"]:
            reason = "authoritative_weather_receipt_source_chain_missing"
        elif receipt.get("stale_cache") is not False:
            reason = "weather_receipt_stale"
        else:
            try:
                parsed_receipt = AuthoritativeWeatherReceipt.model_validate(receipt)
            except ValidationError:
                reason = "authoritative_weather_receipt_invalid"
            else:
                receipt_source_chain = list(parsed_receipt.source_chain)
                observed_at = parsed_receipt.observed_at_utc
                expires_at = parsed_receipt.expires_at_utc
                current_time = datetime.now(timezone.utc)
                if parsed_receipt.authority_source != authority_source:
                    reason = "authoritative_weather_receipt_mismatch"
                elif parsed_receipt.source != authority_source or receipt_source_chain != source_chain:
                    reason = "authoritative_weather_receipt_mismatch"
                elif observed_at.tzinfo is None or expires_at.tzinfo is None:
                    reason = "weather_freshness_missing"
                elif observed_at > current_time or expires_at <= observed_at:
                    reason = "weather_freshness_invalid"
                elif expires_at <= current_time:
                    reason = "weather_receipt_expired"
                elif context.latitude is not None and parsed_receipt.latitude != context.latitude:
                    reason = "authoritative_weather_receipt_mismatch"
                elif context.longitude is not None and parsed_receipt.longitude != context.longitude:
                    reason = "authoritative_weather_receipt_mismatch"
                elif not _receipt_values_match(weather, parsed_receipt):
                    reason = "authoritative_weather_receipt_mismatch"
                elif context.require_selection_binding and parsed_receipt.selection_id is None:
                    reason = "weather_selection_id_missing"
                elif context.require_selection_binding and str(parsed_receipt.selection_id) != context.selection_id:
                    reason = "selection_snapshot_mismatch"
                elif context.require_selection_binding and context.selection_snapshot_id != context.selection_id:
                    reason = "selection_snapshot_mismatch"
                else:
                    reason = None

    authoritative = bool(available and weather.get("authoritative") is True and not stale_cache and reason is None)
    accepted_receipt = parsed_receipt.model_dump(mode="json") if authoritative and parsed_receipt else receipt
    return {
        "available": available,
        "authoritative": authoritative,
        "official_available": authoritative,
        "status": "official_verified" if authoritative else ("unavailable" if not available else "estimated"),
        "source": weather.get("source"),
        "source_chain": source_chain,
        "authority_source": authority_source,
        "profile_source": weather.get("profile_source"),
        "stale_cache": stale_cache,
        "reason": reason,
        "upper_air_available": upper_air_available,
        "wind_profiler_available": wind_profiler_available,
        "selection_id": str(parsed_receipt.selection_id) if authoritative and parsed_receipt and parsed_receipt.selection_id else receipt.get("selection_id"),
        "receipt": accepted_receipt or None,
        "provenance_status": "verified" if authoritative else "rejected",
    }


def bind_correlation(payload: Optional[Dict[str, Any]], correlation_id: str) -> Optional[Dict[str, Any]]:
    """Bind one backend correlation ID to an accepted evidence object and its receipts."""
    if payload is None:
        return None
    bound = dict(payload)
    bound["correlation_id"] = correlation_id
    for receipt_name in ("receipt", "official_footprint_receipt", "official_registry_receipt"):
        receipt = bound.get(receipt_name)
        if isinstance(receipt, dict):
            bound_receipt = dict(receipt)
            bound_receipt["correlation_id"] = correlation_id
            bound[receipt_name] = bound_receipt
    return bound
