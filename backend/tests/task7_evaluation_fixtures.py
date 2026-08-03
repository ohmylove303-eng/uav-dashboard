from typing import Optional


SELECTION_ID = "00000000-0000-4000-8000-000000000007"
OTHER_SELECTION_ID = "00000000-0000-4000-8000-000000000008"


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


def authoritative_weather() -> dict:
    return {
        "available": True,
        "authoritative": True,
        "authority_source": "kma_surface_observation",
        "source": "kma_surface_observation",
        "source_chain": ["kma_surface_observation"],
        "profile_source": "surface_only",
        "stale_cache": False,
        "wind_speed": 5.0,
        "gust_speed": 7.5,
        "wind_direction": 90,
        "visibility": 12.0,
        "precipitation_prob": 0,
        "weather_code": 0,
        "temperature": 20,
        "dew_point": 14,
        "humidity": 50,
        "cloud_cover": 20,
        "sunrise": "06:00",
        "sunset": "18:00",
    }


def official_canyon(
    object_selection_id: Optional[str],
    receipt_selection_id: Optional[str],
) -> dict:
    receipt = {
        "kind": "official_canyon_width",
        "target_geometry_receipt": True,
        "opposing_geometry_receipt": True,
        "road_geometry_receipt": True,
        "road_crossing_verified": True,
        "source_chain": ["official_canyon_width"],
    }
    result = {
        "available": True,
        "official_available": True,
        "facade_gap_m": 27.0,
        "source": "official_canyon_width",
        "source_chain": ["official_canyon_width"],
        "receipt": receipt,
    }
    if object_selection_id is not None:
        result["selection_id"] = object_selection_id
    if receipt_selection_id is not None:
        receipt["selection_id"] = receipt_selection_id
    return result
