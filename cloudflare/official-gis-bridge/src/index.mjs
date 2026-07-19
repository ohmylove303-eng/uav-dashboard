import { measureFacadeGap } from "./geometry.mjs";

const MAP_WFS_ENDPOINT = "https://map.vworld.kr/js/wfs.do";
const BUILDING_LAYER = "lt_c_spbd";
const ROAD_LAYER = "lt_l_n3a0020000";

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function authorizationMatches(value, token) {
  const expected = `Bearer ${token ?? ""}`;
  if (!token || !value || value.length !== expected.length) return false;
  let difference = 0;
  for (let index = 0; index < expected.length; index += 1) difference |= expected.charCodeAt(index) ^ value.charCodeAt(index);
  return difference === 0;
}

function finiteCoordinate(value, minimum, maximum) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null;
}

function lonLatToMercator(lon, lat) {
  const originShift = 20_037_508.34;
  const boundedLat = Math.max(-89.5, Math.min(89.5, lat));
  const x = lon * originShift / 180;
  const y = Math.log(Math.tan((90 + boundedLat) * Math.PI / 360)) / (Math.PI / 180) * originShift / 180;
  return [x, y];
}

function roadBbox(lat, lon, radiusM = 500) {
  const [x, y] = lonLatToMercator(lon, lat);
  return `${x - radiusM},${y - radiusM},${x + radiusM},${y + radiusM}`;
}

function pointInRing(lon, lat, ring) {
  if (ring.length < 4) return false;
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
    const [x, y] = ring[index];
    const [previousX, previousY] = ring[previous];
    if ((y > lat) !== (previousY > lat) && lon < (previousX - x) * (lat - y) / ((previousY - y) || 1e-12) + x) inside = !inside;
  }
  return inside;
}

function ringFromGeometry(geometry) {
  if (!geometry || typeof geometry !== "object") return null;
  if (geometry.type === "Polygon") return geometry.coordinates?.[0] ?? null;
  if (geometry.type === "MultiPolygon") return geometry.coordinates?.[0]?.[0] ?? null;
  return null;
}

function buildingName(properties = {}) {
  for (const key of ["buld_nm", "buld_nm_dc", "name", "name_ko", "bd_nm"]) {
    const value = String(properties[key] ?? "").trim();
    if (value) return value;
  }
  return null;
}

function buildingId(feature, index) {
  const properties = feature.properties ?? {};
  for (const key of ["bd_mgt_sn", "bld_mgt_sn", "pk", "id", "fid"]) {
    if (properties[key] !== undefined && String(properties[key]).trim()) return String(properties[key]);
  }
  return String(feature.id ?? `vworld-building-${index}`);
}

function extractBuildings(payload) {
  return (payload?.features ?? []).flatMap((feature, index) => {
    const ring = ringFromGeometry(feature.geometry);
    return Array.isArray(ring) && ring.length >= 4 ? [{
      id: buildingId(feature, index),
      name: buildingName(feature.properties),
      ring,
    }] : [];
  });
}

function linesFromGeometry(geometry) {
  if (!geometry || typeof geometry !== "object") return [];
  if (geometry.type === "LineString") return [geometry.coordinates];
  if (geometry.type === "MultiLineString") return geometry.coordinates;
  return [];
}

function parseWktLines(value) {
  const text = String(value ?? "").trim();
  if (!text) return [];
  const matches = text.match(/(?:LINESTRING|MULTILINESTRING)\s*\((.*)\)$/i);
  if (!matches) return [];
  const chunks = text.toUpperCase().startsWith("MULTILINESTRING") ? matches[1].split("),(") : [matches[1]];
  return chunks.map((chunk) => chunk.replaceAll("(", "").replaceAll(")", "").split(",").flatMap((pair) => {
    const [x, y] = pair.trim().split(/\s+/).map(Number);
    return Number.isFinite(x) && Number.isFinite(y) ? [[x, y]] : [];
  })).filter((line) => line.length >= 2);
}

function distanceToSegment([px, py], [ax, ay], [bx, by]) {
  const dx = bx - ax;
  const dy = by - ay;
  const ratio = dx === 0 && dy === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + ratio * dx), py - (ay + ratio * dy));
}

function roadDistance(point, paths) {
  const distances = paths.flatMap((path) => path.slice(1).map((end, index) => distanceToSegment(point, path[index], end)));
  return distances.length ? Math.min(...distances) : Number.POSITIVE_INFINITY;
}

function roadName(properties = {}) {
  for (const key of ["rdnm", "name", "road_name", "rd_nm", "rn"]) {
    const value = String(properties[key] ?? "").trim();
    if (value) return value;
  }
  return null;
}

function positiveNumber(value) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").replace(/[mM]$/, ""));
  return Number.isFinite(parsed) && parsed > 0 ? Number(parsed.toFixed(1)) : null;
}

function selectRoad(payload, lat, lon, requestedRoadName) {
  const point = lonLatToMercator(lon, lat);
  const normalizedRequest = String(requestedRoadName ?? "").trim();
  const candidates = (payload?.features ?? []).flatMap((feature) => {
    const properties = feature.properties ?? {};
    const paths = linesFromGeometry(feature.geometry);
    const resolvedPaths = paths.length ? paths : parseWktLines(properties.ag_geom);
    if (!resolvedPaths.length) return [];
    const name = roadName(properties);
    return [{
      name,
      paths: resolvedPaths,
      rightOfWayWidthM: positiveNumber(properties.rvwd),
      matchesRequestedName: Boolean(normalizedRequest && name && (name === normalizedRequest || name.includes(normalizedRequest))),
      distanceM: roadDistance(point, resolvedPaths),
    }];
  });
  candidates.sort((first, second) => Number(second.matchesRequestedName) - Number(first.matchesRequestedName) || first.distanceM - second.distanceM);
  return candidates[0] ?? null;
}

function unavailable(reason, road = null, target = null) {
  const sourceChain = ["vworld_wfs", "official_canyon_width_unavailable"];
  return {
    available: false,
    official_available: false,
    facade_gap_m: null,
    effective_canyon_width_m: null,
    official_road_right_of_way_width_m: road?.rightOfWayWidthM ?? null,
    road_name: road?.name ?? null,
    target_building: target,
    opposing_building: null,
    road_crossing_verified: false,
    normal_alignment: null,
    target_point: null,
    opposing_point: null,
    source: "official_canyon_width_unavailable",
    source_chain: sourceChain,
    reason,
    receipt: {
      kind: "official_canyon_width_unavailable",
      target_geometry_receipt: Boolean(target?.geometry_receipt),
      opposing_geometry_receipt: false,
      road_geometry_receipt: Boolean(road),
      road_crossing_verified: false,
      source_chain: sourceChain,
    },
  };
}

async function fetchJson(fetchImpl, url, referer, source) {
  const response = await fetchImpl(url, { headers: { accept: "application/json", referer, "user-agent": "uav-official-gis-bridge/1.0" } });
  if (!response.ok) throw new Error(`${source}_upstream_status_${response.status}`);
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${source}_upstream_invalid_json`);
  }
}

function buildingRequest(lat, lon, env) {
  const url = new URL(MAP_WFS_ENDPOINT);
  url.search = new URLSearchParams({
    SERVICE: "WFS", REQUEST: "GetFeature", VERSION: "1.1.0", TYPENAME: env.VWORLD_WFS_TYPENAME || BUILDING_LAYER,
    MAXFEATURES: "100", SRSNAME: "EPSG:3857", OUTPUT: "application/json", EXCEPTIONS: "text/xml",
    BBOX: roadBbox(lat, lon, 180), APIKEY: env.VWORLD_DATA_API_KEY,
  }).toString();
  return url;
}

function roadRequest(lat, lon, env) {
  const url = new URL(MAP_WFS_ENDPOINT);
  url.search = new URLSearchParams({
    SERVICE: "WFS", REQUEST: "GetFeature", VERSION: "1.1.0", TYPENAME: ROAD_LAYER, MAXFEATURES: "80",
    SRSNAME: "EPSG:3857", OUTPUT: "application/json", EXCEPTIONS: "text/xml", PROPERTYNAME: "rvwd,rdln,rdnm,ag_geom",
    BBOX: roadBbox(lat, lon), APIKEY: env.VWORLD_DATA_API_KEY, DOMAIN: env.VWORLD_REFERER,
  }).toString();
  return url;
}

async function canyonEvidence(lat, lon, roadNameValue, env, fetchImpl) {
  if (!env.VWORLD_DATA_API_KEY || !env.VWORLD_REFERER) return unavailable("missing_vworld_data_api_key");
  let buildingPayload;
  let roadPayload;
  try {
    buildingPayload = await fetchJson(fetchImpl, buildingRequest(lat, lon, env), env.VWORLD_REFERER, "building");
    roadPayload = await fetchJson(fetchImpl, roadRequest(lat, lon, env), env.VWORLD_REFERER, "road");
  } catch (error) {
    return unavailable(error instanceof Error ? error.message : "upstream_request_failed");
  }
  const buildings = extractBuildings(buildingPayload);
  const clickPoint = lonLatToMercator(lon, lat);
  const targetMatches = buildings.filter((building) => pointInRing(clickPoint[0], clickPoint[1], building.ring));
  const target = targetMatches.length === 1 ? targetMatches[0] : null;
  const targetReceipt = target ? { id: target.id, name: target.name, geometry_receipt: true, selection_match: true } : null;
  if (!target) return unavailable("target_official_building_not_selected", null, { id: "target-building", name: null, geometry_receipt: false, selection_match: false });
  const road = selectRoad(roadPayload, lat, lon, roadNameValue);
  if (!road) return unavailable("official_road_geometry_not_matched", null, targetReceipt);
  const measurement = measureFacadeGap({ targetRing: target.ring, roadPath: road.paths[0], buildings: buildings.filter((building) => building.id !== target.id) });
  if (!measurement.available) return unavailable(measurement.reason, road, targetReceipt);
  const sourceChain = ["vworld_wfs", "official_building_collection", "official_road_right_of_way", ROAD_LAYER, "official_canyon_width"];
  return {
    available: true,
    official_available: true,
    facade_gap_m: measurement.facadeGapM,
    effective_canyon_width_m: measurement.facadeGapM,
    official_road_right_of_way_width_m: road.rightOfWayWidthM,
    road_name: road.name,
    target_building: targetReceipt,
    opposing_building: { id: measurement.opposingBuildingId, name: measurement.opposingBuildingName, geometry_receipt: true },
    road_crossing_verified: true,
    normal_alignment: measurement.normalAlignment,
    target_point: measurement.targetPoint,
    opposing_point: measurement.opposingPoint,
    source: "official_canyon_width",
    source_chain: sourceChain,
    reason: null,
    receipt: { kind: "official_canyon_width", target_geometry_receipt: true, opposing_geometry_receipt: true, road_geometry_receipt: true, road_crossing_verified: true, source_chain: sourceChain },
  };
}

export function createWorker({ fetchImpl = fetch } = {}) {
  return {
    async fetch(request, env) {
      const url = new URL(request.url);
      if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
      if (url.pathname === "/health") return json({ status: "ok", service: "official-gis-bridge" });
      if (url.pathname !== "/api/canyon-width") return json({ error: "not_found" }, 404);
      if (!authorizationMatches(request.headers.get("authorization"), env.OFFICIAL_GIS_BRIDGE_TOKEN)) return json({ error: "official_gis_bridge_authorization_required" }, 401);
      const lat = finiteCoordinate(url.searchParams.get("lat"), -90, 90);
      const lon = finiteCoordinate(url.searchParams.get("lon"), -180, 180);
      if (lat === null || lon === null) return json({ error: "valid_lat_and_lon_are_required" }, 400);
      return json(await canyonEvidence(lat, lon, url.searchParams.get("road_name"), env, fetchImpl));
    },
  };
}

export default createWorker({});
