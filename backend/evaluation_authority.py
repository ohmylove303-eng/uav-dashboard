"""Validation and binding for one authoritative evaluation snapshot."""

from datetime import datetime, timezone
from typing import Any, Dict, Final, List, Optional


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


def _normalize_source_chain(*parts: Any) -> List[str]:
    chain: List[str] = []
    for part in parts:
        values = part if isinstance(part, (list, tuple, set)) else [part]
        for value in values:
            token = str(value or "").strip()
            if token and token not in chain:
                chain.append(token)
    return chain


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _receipt_values_match(weather: Dict[str, Any], receipt: Dict[str, Any]) -> bool:
    values = receipt.get("values")
    return bool(
        isinstance(values, dict)
        and all(field in values and values[field] == weather.get(field) for field in DECISION_WEATHER_FIELDS)
    )


def build_weather_evidence(
    weather: Dict[str, Any],
    upper_air: Optional[Dict[str, Any]] = None,
    wind_profiler: Optional[Dict[str, Any]] = None,
    *,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    selection_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return decision authority only for a complete, fresh KMA receipt."""
    source_chain = _normalize_source_chain(weather.get("source_chain") or [], weather.get("source"))
    stale_cache = bool(weather.get("stale_cache", False))
    available = bool(weather.get("available", "weather_unavailable" not in source_chain))
    authority_source = weather.get("authority_source")
    receipt = dict(weather.get("receipt") or {}) if isinstance(weather.get("receipt"), dict) else {}
    reason = weather.get("reason")

    if not available:
        reason = reason or "authoritative_weather_missing"
    elif stale_cache:
        reason = "weather_cache_expired"
    elif source_chain and not any(source in KMA_WEATHER_SOURCES for source in source_chain):
        reason = "authoritative_weather_untrusted"
    elif not authority_source:
        reason = "authoritative_weather_source_missing"
    elif authority_source not in KMA_WEATHER_SOURCES or authority_source not in source_chain:
        reason = "authoritative_weather_untrusted"
    elif not receipt:
        reason = "authoritative_weather_receipt_missing"
    elif receipt.get("kind") != "kma_weather_observation" or not receipt.get("receipt_id"):
        reason = "authoritative_weather_receipt_invalid"
    elif receipt.get("authority_source") != authority_source:
        reason = "authoritative_weather_receipt_mismatch"
    else:
        observed_at = _parse_utc_timestamp(receipt.get("observed_at_utc"))
        expires_at = _parse_utc_timestamp(receipt.get("expires_at_utc"))
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if observed_at is None or expires_at is None:
            reason = "weather_freshness_missing"
        elif observed_at > current_time or expires_at <= observed_at:
            reason = "weather_freshness_invalid"
        elif expires_at <= current_time:
            reason = "weather_receipt_expired"
        elif latitude is not None and receipt.get("latitude") != latitude:
            reason = "authoritative_weather_receipt_mismatch"
        elif longitude is not None and receipt.get("longitude") != longitude:
            reason = "authoritative_weather_receipt_mismatch"
        elif not _receipt_values_match(weather, receipt):
            reason = "authoritative_weather_receipt_mismatch"
        elif receipt.get("selection_id") not in (None, selection_id):
            reason = "selection_snapshot_mismatch"
        else:
            reason = None

    authoritative = bool(available and not stale_cache and reason is None)
    if authoritative and selection_id is not None:
        receipt["selection_id"] = selection_id
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
        "upper_air_available": bool(upper_air),
        "wind_profiler_available": bool(wind_profiler),
        "selection_id": selection_id if authoritative else receipt.get("selection_id"),
        "receipt": receipt or None,
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
