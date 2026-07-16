from __future__ import annotations

from math import hypot, isfinite
from typing import Sequence, TypedDict


class BuildingGeometry(TypedDict, total=False):
    id: str
    name: str | None
    ring: list[list[float]]


class FacadeGapMeasurement(TypedDict):
    available: bool
    facade_gap_m: float | None
    opposing_building_id: str | None
    opposing_building_name: str | None
    road_crossing_verified: bool
    normal_alignment: float | None
    target_point: list[float] | None
    opposing_point: list[float] | None
    reason: str | None


Point = tuple[float, float]


def _points(raw_points: Sequence[Sequence[float]]) -> list[Point]:
    points: list[Point] = []
    for raw_point in raw_points:
        if len(raw_point) < 2:
            continue
        x, y = float(raw_point[0]), float(raw_point[1])
        if isfinite(x) and isfinite(y):
            points.append((x, y))
    return points


def _segments(points: Sequence[Point]) -> list[tuple[Point, Point]]:
    return list(zip(points, points[1:]))


def _centroid(points: Sequence[Point]) -> Point:
    count = len(points)
    return (sum(point[0] for point in points) / count, sum(point[1] for point in points) / count)


def _cross(a: Point, b: Point, c: Point) -> float:
    return ((b[0] - a[0]) * (c[1] - a[1])) - ((b[1] - a[1]) * (c[0] - a[0]))


def _point_on_segment(point: Point, start: Point, end: Point, tolerance: float = 1e-6) -> bool:
    if abs(_cross(start, end, point)) > tolerance:
        return False
    return (
        min(start[0], end[0]) - tolerance <= point[0] <= max(start[0], end[0]) + tolerance
        and min(start[1], end[1]) - tolerance <= point[1] <= max(start[1], end[1]) + tolerance
    )


def _segments_intersect(first: Point, second: Point, third: Point, fourth: Point) -> bool:
    first_side = _cross(first, second, third)
    second_side = _cross(first, second, fourth)
    third_side = _cross(third, fourth, first)
    fourth_side = _cross(third, fourth, second)
    if first_side == 0.0 and _point_on_segment(third, first, second):
        return True
    if second_side == 0.0 and _point_on_segment(fourth, first, second):
        return True
    if third_side == 0.0 and _point_on_segment(first, third, fourth):
        return True
    if fourth_side == 0.0 and _point_on_segment(second, third, fourth):
        return True
    return (first_side > 0.0) != (second_side > 0.0) and (third_side > 0.0) != (fourth_side > 0.0)


def _closest_point_on_segment(point: Point, start: Point, end: Point) -> Point:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = (dx * dx) + (dy * dy)
    if denominator == 0.0:
        return start
    ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator
    bounded_ratio = max(0.0, min(1.0, ratio))
    return (start[0] + (bounded_ratio * dx), start[1] + (bounded_ratio * dy))


def _distance(first: Point, second: Point) -> float:
    return hypot(first[0] - second[0], first[1] - second[1])


def _closest_segment_pair(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
) -> tuple[float, Point, Point]:
    if _segments_intersect(first_start, first_end, second_start, second_end):
        return (0.0, first_start, first_start)
    candidates = (
        (first_start, _closest_point_on_segment(first_start, second_start, second_end)),
        (first_end, _closest_point_on_segment(first_end, second_start, second_end)),
        (_closest_point_on_segment(second_start, first_start, first_end), second_start),
        (_closest_point_on_segment(second_end, first_start, first_end), second_end),
    )
    closest = min(candidates, key=lambda pair: _distance(pair[0], pair[1]))
    return (_distance(closest[0], closest[1]), closest[0], closest[1])


def _closest_rings(first_ring: Sequence[Point], second_ring: Sequence[Point]) -> tuple[float, Point, Point]:
    candidates = [
        _closest_segment_pair(first_start, first_end, second_start, second_end)
        for first_start, first_end in _segments(first_ring)
        for second_start, second_end in _segments(second_ring)
    ]
    return min(candidates, key=lambda candidate: candidate[0])


def _closest_road_segment(point: Point, road_segments: Sequence[tuple[Point, Point]]) -> tuple[Point, Point]:
    return min(
        road_segments,
        key=lambda segment: _distance(point, _closest_point_on_segment(point, segment[0], segment[1])),
    )


def _signed_road_side(point: Point, road_segment: tuple[Point, Point]) -> float:
    return _cross(road_segment[0], road_segment[1], point)


def _road_crossing_is_normal(
    target_point: Point,
    opposing_point: Point,
    road_segments: Sequence[tuple[Point, Point]],
) -> tuple[bool, float | None]:
    dx, dy = opposing_point[0] - target_point[0], opposing_point[1] - target_point[1]
    gap_length = hypot(dx, dy)
    if gap_length == 0.0:
        return (False, None)
    for road_start, road_end in road_segments:
        if not _segments_intersect(target_point, opposing_point, road_start, road_end):
            continue
        road_dx, road_dy = road_end[0] - road_start[0], road_end[1] - road_start[1]
        road_length = hypot(road_dx, road_dy)
        if road_length == 0.0:
            continue
        parallel_component = abs((dx * road_dx + dy * road_dy) / (gap_length * road_length))
        normal_alignment = 1.0 - parallel_component
        return (normal_alignment >= 0.707, round(normal_alignment, 4))
    return (False, None)


def measure_facade_gap(
    target_ring: Sequence[Sequence[float]],
    road_path: Sequence[Sequence[float]],
    buildings: Sequence[BuildingGeometry],
) -> FacadeGapMeasurement:
    target_points = _points(target_ring)
    road_points = _points(road_path)
    target_segments = _segments(target_points)
    road_segments = _segments(road_points)
    if len(target_points) < 4 or not target_segments:
        return {
            "available": False,
            "facade_gap_m": None,
            "opposing_building_id": None,
            "opposing_building_name": None,
            "road_crossing_verified": False,
            "normal_alignment": None,
            "target_point": None,
            "opposing_point": None,
            "reason": "target_official_building_geometry_invalid",
        }
    if len(road_points) < 2 or not road_segments:
        return {
            "available": False,
            "facade_gap_m": None,
            "opposing_building_id": None,
            "opposing_building_name": None,
            "road_crossing_verified": False,
            "normal_alignment": None,
            "target_point": None,
            "opposing_point": None,
            "reason": "official_road_geometry_not_matched",
        }

    target_center = _centroid(target_points[:-1] if target_points[0] == target_points[-1] else target_points)
    target_road_segment = _closest_road_segment(target_center, road_segments)
    target_side = _signed_road_side(target_center, target_road_segment)
    if target_side == 0.0:
        return {
            "available": False,
            "facade_gap_m": None,
            "opposing_building_id": None,
            "opposing_building_name": None,
            "road_crossing_verified": False,
            "normal_alignment": None,
            "target_point": None,
            "opposing_point": None,
            "reason": "target_building_road_side_ambiguous",
        }

    measurements: list[tuple[float, Point, Point, BuildingGeometry, float]] = []
    for building in buildings:
        candidate_points = _points(building["ring"])
        candidate_segments = _segments(candidate_points)
        if len(candidate_points) < 4 or not candidate_segments:
            continue
        candidate_center = _centroid(candidate_points[:-1] if candidate_points[0] == candidate_points[-1] else candidate_points)
        candidate_side = _signed_road_side(candidate_center, target_road_segment)
        if candidate_side == 0.0 or (candidate_side > 0.0) == (target_side > 0.0):
            continue
        gap_m, target_point, opposing_point = _closest_rings(target_points, candidate_points)
        crossing_verified, normal_alignment = _road_crossing_is_normal(target_point, opposing_point, road_segments)
        if not crossing_verified or normal_alignment is None:
            continue
        measurements.append((gap_m, target_point, opposing_point, building, normal_alignment))

    if not measurements:
        return {
            "available": False,
            "facade_gap_m": None,
            "opposing_building_id": None,
            "opposing_building_name": None,
            "road_crossing_verified": False,
            "normal_alignment": None,
            "target_point": None,
            "opposing_point": None,
            "reason": "opposing_official_building_not_matched",
        }

    gap_m, target_point, opposing_point, opposing_building, normal_alignment = min(measurements, key=lambda result: result[0])
    return {
        "available": True,
        "facade_gap_m": round(gap_m, 1),
        "opposing_building_id": opposing_building["id"],
        "opposing_building_name": opposing_building.get("name"),
        "road_crossing_verified": True,
        "normal_alignment": normal_alignment,
        "target_point": [round(target_point[0], 3), round(target_point[1], 3)],
        "opposing_point": [round(opposing_point[0], 3), round(opposing_point[1], 3)],
        "reason": None,
    }
