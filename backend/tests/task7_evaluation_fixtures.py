from datetime import datetime, timedelta, timezone
from typing import Optional


SELECTION_ID = "00000000-0000-4000-8000-000000000007"
OTHER_SELECTION_ID = "00000000-0000-4000-8000-000000000008"
CANYON_RECEIPT_IDS = {
    "target_geometry": "d09405f8-c168-5ba7-b928-5102ed0a0d44",
    "opposing_geometry": "ec3eff5c-dda4-55b5-b659-865865b8c3b6",
    "road_geometry": "1c342204-fb71-5448-ad27-e7298cf93647",
    "road_crossing": "8bf5b12e-436a-5889-a72f-4ff6d950f98c",
    "facade_gap": "4cce213b-98ba-5118-ad95-8e52c084c72b",
}
CANYON_RECEIPT_SOURCES = {
    part: "direct_vworld_official_receipt" for part in CANYON_RECEIPT_IDS
}


def forged_client_building_fields() -> dict:
    return {
        "building_height": 40.0,
        "building_source": "client_forged_official",
        "building_profile_source": "official_verified",
        "building_source_chain": ["client_forged_official"],
        "building_confidence": 0.99,
        "building_evidence": {
            "available": True,
            "official_available": True,
            "status": "official_verified",
            "source": "client_forged_official",
            "source_chain": ["client_forged_official"],
            "receipt": {
                "kind": "official_building_height",
                "geometry_receipt": True,
                "selection_match": True,
                "source_chain": ["client_forged_official"],
            },
        },
    }


def server_building(*, official_height: bool = True) -> dict:
    properties = {"gro_flo_co": 10}
    if official_height:
        properties["buld_hg"] = 40.0
    return {
        "available": True,
        "official_footprint_available": True,
        "official_geometry_receipt": True,
        "official_selection_match": True,
        "source": "vworld_wfs",
        "source_chain": ["vworld_wfs", "molit_building_hub"],
        "native_feature_id": "lt_c_spbd.7",
        "properties": properties,
        "field_sources": {
            "height_m": {"source": "molit_building_hub", "status": "official_verified"},
        },
        "building_selection": {
            "selection_id": SELECTION_ID,
            "status": "official_verified",
            "native_feature_id": "lt_c_spbd.7",
            "official_footprint_receipt": {
                "kind": "vworld_building_footprint",
                "native_feature_id": "lt_c_spbd.7",
                "point_inside": True,
            },
            "official_registry_receipt": {
                "kind": "molit_building_hub_title",
                "record_id": "record-7",
            },
        },
    }


def authoritative_weather(
    *,
    latitude: float = 37.5662952,
    longitude: float = 126.9779451,
    selection_id: Optional[str] = None,
) -> dict:
    observed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    values = {
        "wind_speed": 5.0,
        "gust_speed": 7.5,
        "visibility": 12.0,
        "precipitation_prob": 0,
        "weather_code": 0,
    }
    receipt = {
        "kind": "kma_weather_observation",
        "receipt_id": "kma-surface-deterministic-fixture",
        "authority_source": "kma_surface_observation",
        "source": "kma_surface_observation",
        "observed_at_utc": observed_at.isoformat(),
        "expires_at_utc": (observed_at + timedelta(hours=1)).isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "values": dict(values),
        "source_chain": ["kma_surface_observation"],
        "stale_cache": False,
    }
    if selection_id is not None:
        receipt["selection_id"] = selection_id
    return {
        "available": True,
        "authoritative": True,
        "authority_source": "kma_surface_observation",
        "source": "kma_surface_observation",
        "source_chain": ["kma_surface_observation"],
        "profile_source": "surface_only",
        "stale_cache": False,
        **values,
        "wind_direction": 90,
        "temperature": 20,
        "dew_point": 14,
        "humidity": 50,
        "cloud_cover": 20,
        "sunrise": "06:00",
        "sunset": "18:00",
        "receipt": receipt,
    }


def official_canyon(
    object_selection_id: Optional[str],
    receipt_selection_id: Optional[str],
    *,
    target_building_id: str = "target-7",
    target_native_feature_id: Optional[str] = "lt_c_spbd.7",
) -> dict:
    receipt = {
        "kind": "official_canyon_width",
        "target_geometry_receipt": True,
        "opposing_geometry_receipt": True,
        "road_geometry_receipt": True,
        "road_crossing_verified": True,
        "source_chain": ["official_canyon_width", "direct_vworld_official_receipt"],
        "receipt_ids": dict(CANYON_RECEIPT_IDS),
        "receipt_sources": dict(CANYON_RECEIPT_SOURCES),
    }
    target_building = {"id": target_building_id, "geometry_receipt": True}
    if target_native_feature_id is not None:
        target_building["native_feature_id"] = target_native_feature_id
        receipt["native_feature_id"] = target_native_feature_id
        receipt["target_native_feature_id"] = target_native_feature_id
    result = {
        "available": True,
        "official_available": True,
        "facade_gap_m": 27.0,
        "effective_canyon_width_m": 27.0,
        "road_crossing_verified": True,
        "source": "direct_vworld_official_receipt",
        "source_chain": ["official_canyon_width", "direct_vworld_official_receipt"],
        "target_building": target_building,
        "opposing_building": {"id": "opposing-7", "geometry_receipt": True},
        "receipt": receipt,
    }
    if object_selection_id is not None:
        result["selection_id"] = object_selection_id
    if receipt_selection_id is not None:
        receipt["selection_id"] = receipt_selection_id
    return result
