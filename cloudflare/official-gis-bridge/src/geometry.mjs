const NORMAL_ALIGNMENT_MINIMUM = 0.707;

function asPoints(rawPoints) {
  return rawPoints
    .filter((point) => Array.isArray(point) && point.length >= 2)
    .map(([x, y]) => [Number(x), Number(y)])
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
}

function comparePointSequences(first, second) {
  for (let index = 0; index < first.length; index += 1) {
    const [firstX, firstY] = first[index];
    const [secondX, secondY] = second[index];
    if (firstX !== secondX) return firstX < secondX ? -1 : 1;
    if (firstY !== secondY) return firstY < secondY ? -1 : 1;
  }
  return first.length - second.length;
}

function normalizedRingKey(points) {
  const ring = points.map(([x, y]) => [x, y]);
  if (ring.length > 1 && ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1]) ring.pop();
  if (ring.length === 0) return [];
  const rotations = [];
  for (const oriented of [ring, [...ring].reverse()]) {
    for (let index = 0; index < oriented.length; index += 1) {
      rotations.push([...oriented.slice(index), ...oriented.slice(0, index)]);
    }
  }
  return rotations.reduce((current, candidate) => comparePointSequences(candidate, current) < 0 ? candidate : current);
}

function officialBuildingTieBreakKey(building, points) {
  const stableId = String(building.stableId ?? building.id ?? "").trim();
  return stableId ? [stableId, normalizedRingKey(points)] : null;
}

function compareFacadeCandidates(first, second) {
  if (first.gap !== second.gap) return first.gap < second.gap ? -1 : 1;
  if (first.stableId !== second.stableId) return first.stableId < second.stableId ? -1 : 1;
  return comparePointSequences(first.normalizedGeometry, second.normalizedGeometry);
}

function segments(points) {
  return points.slice(1).map((point, index) => [points[index], point]);
}

function cross([ax, ay], [bx, by], [cx, cy]) {
  return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
}

function distance([ax, ay], [bx, by]) {
  return Math.hypot(ax - bx, ay - by);
}

function centroid(points) {
  const openRing = points[0][0] === points.at(-1)[0] && points[0][1] === points.at(-1)[1] ? points.slice(0, -1) : points;
  return [
    openRing.reduce((sum, [x]) => sum + x, 0) / openRing.length,
    openRing.reduce((sum, [, y]) => sum + y, 0) / openRing.length,
  ];
}

function pointOnSegment(point, start, end) {
  if (Math.abs(cross(start, end, point)) > 1e-6) return false;
  return point[0] >= Math.min(start[0], end[0]) - 1e-6 && point[0] <= Math.max(start[0], end[0]) + 1e-6
    && point[1] >= Math.min(start[1], end[1]) - 1e-6 && point[1] <= Math.max(start[1], end[1]) + 1e-6;
}

function segmentsIntersect(first, second, third, fourth) {
  const firstSide = cross(first, second, third);
  const secondSide = cross(first, second, fourth);
  const thirdSide = cross(third, fourth, first);
  const fourthSide = cross(third, fourth, second);
  if (firstSide === 0 && pointOnSegment(third, first, second)) return true;
  if (secondSide === 0 && pointOnSegment(fourth, first, second)) return true;
  if (thirdSide === 0 && pointOnSegment(first, third, fourth)) return true;
  if (fourthSide === 0 && pointOnSegment(second, third, fourth)) return true;
  return (firstSide > 0) !== (secondSide > 0) && (thirdSide > 0) !== (fourthSide > 0);
}

function closestPointOnSegment(point, start, end) {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const denominator = dx * dx + dy * dy;
  if (denominator === 0) return start;
  const ratio = Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator));
  return [start[0] + ratio * dx, start[1] + ratio * dy];
}

function closestSegmentPair(firstStart, firstEnd, secondStart, secondEnd) {
  if (segmentsIntersect(firstStart, firstEnd, secondStart, secondEnd)) return [0, firstStart, firstStart];
  const candidates = [
    [firstStart, closestPointOnSegment(firstStart, secondStart, secondEnd)],
    [firstEnd, closestPointOnSegment(firstEnd, secondStart, secondEnd)],
    [closestPointOnSegment(secondStart, firstStart, firstEnd), secondStart],
    [closestPointOnSegment(secondEnd, firstStart, firstEnd), secondEnd],
  ];
  const [firstPoint, secondPoint] = candidates.reduce((current, candidate) => distance(...candidate) < distance(...current) ? candidate : current);
  return [distance(firstPoint, secondPoint), firstPoint, secondPoint];
}

function closestRings(firstRing, secondRing) {
  const candidates = [];
  for (const [firstStart, firstEnd] of segments(firstRing)) {
    for (const [secondStart, secondEnd] of segments(secondRing)) {
      candidates.push(closestSegmentPair(firstStart, firstEnd, secondStart, secondEnd));
    }
  }
  return candidates.reduce((current, candidate) => candidate[0] < current[0] ? candidate : current);
}

function closestRoadSegment(point, roadSegments) {
  return roadSegments.reduce((current, candidate) => (
    distance(point, closestPointOnSegment(point, ...candidate)) < distance(point, closestPointOnSegment(point, ...current)) ? candidate : current
  ));
}

function roadCrossingIsNormal(targetPoint, opposingPoint, roadSegments) {
  const dx = opposingPoint[0] - targetPoint[0];
  const dy = opposingPoint[1] - targetPoint[1];
  const gapLength = Math.hypot(dx, dy);
  if (gapLength === 0) return [false, null];
  for (const [roadStart, roadEnd] of roadSegments) {
    if (!segmentsIntersect(targetPoint, opposingPoint, roadStart, roadEnd)) continue;
    const roadDx = roadEnd[0] - roadStart[0];
    const roadDy = roadEnd[1] - roadStart[1];
    const roadLength = Math.hypot(roadDx, roadDy);
    if (roadLength === 0) continue;
    const normalAlignment = 1 - Math.abs((dx * roadDx + dy * roadDy) / (gapLength * roadLength));
    return [normalAlignment >= NORMAL_ALIGNMENT_MINIMUM, Number(normalAlignment.toFixed(4))];
  }
  return [false, null];
}

function unavailable(reason) {
  return {
    available: false,
    facadeGapM: null,
    opposingBuildingId: null,
    opposingBuildingName: null,
    roadCrossingVerified: false,
    normalAlignment: null,
    targetPoint: null,
    opposingPoint: null,
    reason,
  };
}

export function measureFacadeGap({ targetRing, roadPath, buildings }) {
  const targetPoints = asPoints(targetRing);
  const roadPoints = asPoints(roadPath);
  const roadSegments = segments(roadPoints);
  if (targetPoints.length < 4) return unavailable("target_official_building_geometry_invalid");
  if (roadSegments.length === 0) return unavailable("official_road_geometry_not_matched");

  const targetRoadSegment = closestRoadSegment(centroid(targetPoints), roadSegments);
  const targetSide = cross(...targetRoadSegment, centroid(targetPoints));
  if (targetSide === 0) return unavailable("target_building_road_side_ambiguous");

  const matches = buildings.flatMap((building) => {
    const candidatePoints = asPoints(building.ring ?? []);
    if (candidatePoints.length < 4) return [];
    const tieBreakKey = officialBuildingTieBreakKey(building, candidatePoints);
    if (tieBreakKey === null) return [];
    const candidateSide = cross(...targetRoadSegment, centroid(candidatePoints));
    if (candidateSide === 0 || (candidateSide > 0) === (targetSide > 0)) return [];
    const [gap, targetPoint, opposingPoint] = closestRings(targetPoints, candidatePoints);
    const [verified, normalAlignment] = roadCrossingIsNormal(targetPoint, opposingPoint, roadSegments);
    const [stableId, normalizedGeometry] = tieBreakKey;
    return verified && normalAlignment !== null ? [{ gap, stableId, normalizedGeometry, targetPoint, opposingPoint, building, normalAlignment }] : [];
  });
  if (matches.length === 0) return unavailable("opposing_official_building_not_matched");

  // Equal facade gaps are ordered by official stable identifier and normalized
  // geometry, so WFS feature order cannot change authoritative evidence.
  const { gap, targetPoint, opposingPoint, building: opposingBuilding, normalAlignment } = matches.reduce(
    (current, candidate) => compareFacadeCandidates(candidate, current) < 0 ? candidate : current,
  );
  return {
    available: true,
    facadeGapM: Number(gap.toFixed(1)),
    opposingBuildingId: String(opposingBuilding.id),
    opposingBuildingName: opposingBuilding.name ?? null,
    roadCrossingVerified: true,
    normalAlignment,
    targetPoint: targetPoint.map((value) => Number(value.toFixed(3))),
    opposingPoint: opposingPoint.map((value) => Number(value.toFixed(3))),
    reason: null,
  };
}
