import assert from "node:assert/strict";
import test from "node:test";

import { createWorker } from "../src/index.mjs";

const selectionId = "9d88e3aa-17c7-4b75-b7a0-a6db69498ca4";
const bridgeUrl = `https://bridge.example/api/canyon-width?lat=0&lon=-0.00006&selection_id=${selectionId}&target_identifier_kind=native_feature_id&target_identifier_value=lt_c_spbd.7`;
const buildingManagementNumber = "1114010300100310000019224";
const buildingManagementBridgeUrl = `https://bridge.example/api/canyon-width?lat=0&lon=-0.00006&selection_id=${selectionId}&target_identifier_kind=bd_mgt_sn&target_identifier_value=${buildingManagementNumber}`;

const buildingFeatures = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      id: "lt_c_spbd.7",
      properties: { bd_mgt_sn: buildingManagementNumber, buld_nm: "Target Building" },
      geometry: {
        type: "Polygon",
        coordinates: [[[-8, -4], [-4, -4], [-4, 4], [-8, 4], [-8, -4]]],
      },
    },
    {
      type: "Feature",
      id: "opposing-building",
      properties: { buld_nm: "Opposing Building" },
      geometry: {
        type: "Polygon",
        coordinates: [[[4, -4], [8, -4], [8, 4], [4, 4], [4, -4]]],
      },
    },
  ],
};

const roadFeatures = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: { rvwd: "49.7", rdnm: "테스트로" },
      geometry: { type: "LineString", coordinates: [[0, -100], [0, 100]] },
    },
  ],
};

function makeWorker(buildings = buildingFeatures) {
  return createWorker({
    fetchImpl: async (url) => new Response(
      String(url).includes("TYPENAME=lt_l_n3a0020000") ? JSON.stringify(roadFeatures) : JSON.stringify(buildings),
      { status: 200, headers: { "content-type": "application/json" } },
    ),
  });
}

test("prefers the Map WFS building collection in EPSG:3857", async () => {
  const urls = [];
  const worker = createWorker({
    fetchImpl: async (url) => {
      urls.push(new URL(url));
      return new Response(
        String(url).includes("TYPENAME=lt_l_n3a0020000") ? JSON.stringify(roadFeatures) : JSON.stringify(buildingFeatures),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    },
  });

  await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );

  const buildingUrl = urls.find((url) => url.searchParams.get("TYPENAME") === "lt_c_spbd");
  assert.equal(buildingUrl?.hostname, "map.vworld.kr");
  assert.equal(buildingUrl?.pathname, "/js/wfs.do");
  assert.equal(buildingUrl?.searchParams.get("SRSNAME"), "EPSG:3857");
  assert.equal(buildingUrl?.searchParams.get("APIKEY"), "vworld-server-only-key");
  assert.equal(buildingUrl?.searchParams.get("DOMAIN"), "uav-dashboard.onrender.com");
});

test("falls back to API WFS without domain when the Map WFS building request fails", async () => {
  const urls = [];
  const worker = createWorker({
    fetchImpl: async (url) => {
      const parsed = new URL(url);
      urls.push(parsed);
      if (parsed.hostname === "map.vworld.kr" && parsed.searchParams.get("TYPENAME") === "lt_c_spbd") {
        return new Response("upstream unavailable", { status: 502 });
      }
      return new Response(
        parsed.searchParams.get("TYPENAME") === "lt_l_n3a0020000" ? JSON.stringify(roadFeatures) : JSON.stringify(buildingFeatures),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    },
  });

  const response = await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );

  const payload = await response.json();
  const apiBuildingUrl = urls.find((url) => url.hostname === "api.vworld.kr" && url.searchParams.get("typename") === "lt_c_spbd");
  assert.equal(payload.available, true);
  assert.equal(apiBuildingUrl?.searchParams.get("key"), "vworld-server-only-key");
  assert.equal(apiBuildingUrl?.searchParams.has("DOMAIN"), false);
  assert.equal(apiBuildingUrl?.searchParams.get("service"), "WFS");
  assert.equal(apiBuildingUrl?.searchParams.get("request"), "GetFeature");
  assert.equal(apiBuildingUrl?.searchParams.get("srsname"), "EPSG:3857");
});

test("does not send a browser referer to the API WFS fallback", async () => {
  const requests = [];
  const worker = createWorker({
    fetchImpl: async (url, options) => {
      const parsed = new URL(url);
      requests.push({ parsed, referer: options?.headers?.referer ?? null });
      if (parsed.hostname === "map.vworld.kr" && parsed.searchParams.get("TYPENAME") === "lt_c_spbd") {
        return new Response("upstream unavailable", { status: 502 });
      }
      return new Response(
        parsed.searchParams.get("TYPENAME") === "lt_l_n3a0020000" ? JSON.stringify(roadFeatures) : JSON.stringify(buildingFeatures),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    },
  });

  await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );

  const apiBuildingRequest = requests.find((request) => request.parsed.hostname === "api.vworld.kr" && request.parsed.searchParams.get("typename") === "lt_c_spbd");
  assert.equal(apiBuildingRequest?.referer, null);
});

const env = {
  OFFICIAL_GIS_BRIDGE_TOKEN: "server-only-token",
  VWORLD_DATA_API_KEY: "vworld-server-only-key",
  VWORLD_REFERER: "https://uav-dashboard.onrender.com",
  VWORLD_WFS_TYPENAME: "lt_c_spbd",
};

test("requires the server-only Render authorization token", async () => {
  const response = await makeWorker().fetch(new Request(bridgeUrl), env);
  const payload = await response.json();

  assert.equal(response.status, 401);
  assert.equal(payload.available, false);
  assert.equal(payload.official_available, false);
  assert.equal(payload.facade_gap_m, null);
  assert.equal(payload.effective_canyon_width_m, null);
  assert.equal(payload.reason, "official_gis_bridge_authorization_required");
  assert.equal(payload.selection_id, selectionId);
  assert.equal(payload.receipt.selection_id, selectionId);
});

test("requires a valid selection UUID before querying official geometry", async () => {
  let upstreamCalls = 0;
  const worker = createWorker({
    fetchImpl: async () => {
      upstreamCalls += 1;
      return new Response("unexpected", { status: 500 });
    },
  });
  const response = await worker.fetch(
    new Request("https://bridge.example/api/canyon-width?lat=0&lon=-0.00006", {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(response.status, 400);
  assert.equal(payload.available, false);
  assert.equal(payload.reason, "invalid_selection_id");
  assert.equal(payload.facade_gap_m, null);
  assert.equal(upstreamCalls, 0);
});

test("requires the Task-7 stable target identifier before querying official geometry", async () => {
  let upstreamCalls = 0;
  const worker = createWorker({
    fetchImpl: async () => {
      upstreamCalls += 1;
      return new Response("unexpected", { status: 500 });
    },
  });
  const response = await worker.fetch(
    new Request(`https://bridge.example/api/canyon-width?lat=0&lon=-0.00006&selection_id=${selectionId}`, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(response.status, 400);
  assert.equal(payload.available, false);
  assert.equal(payload.reason, "invalid_target_identifier");
  assert.equal(payload.facade_gap_m, null);
  assert.equal(upstreamCalls, 0);
});

test("returns a verified facade gap instead of promoting official road right-of-way as a canyon width", async () => {
  const response = await makeWorker().fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(payload.available, true);
  assert.equal(payload.official_available, true);
  assert.equal(payload.source, "official_gis_bridge_receipt");
  assert.equal(payload.selection_id, selectionId);
  assert.notEqual(payload.facade_gap_m, 49.7);
  assert.equal(payload.official_road_right_of_way_width_m, 49.7);
  assert.equal(payload.receipt.road_crossing_verified, true);
  assert.equal(payload.receipt.selection_id, selectionId);
  assert.equal(payload.target_building.native_feature_id, "lt_c_spbd.7");
  assert.equal(payload.target_building.bd_mgt_sn, buildingManagementNumber);
  assert.equal(payload.receipt.target_native_feature_id, "lt_c_spbd.7");
  assert.equal(payload.receipt.target_bd_mgt_sn, buildingManagementNumber);
  assert.deepEqual(new Set(Object.values(payload.receipt.receipt_sources)), new Set(["official_gis_bridge_receipt"]));
  assert.deepEqual(Object.keys(payload.receipt.receipt_ids).sort(), [
    "facade_gap",
    "opposing_geometry",
    "road_crossing",
    "road_geometry",
    "target_geometry",
  ]);
  for (const receiptId of Object.values(payload.receipt.receipt_ids)) {
    assert.match(receiptId, /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  }
  assert.equal(payload.target_building.id, "lt_c_spbd.7");
  assert.equal(payload.opposing_building.id, "opposing-building");
});

test("accepts a Task-7 building management selection without relabeling the native feature ID", async () => {
  const response = await makeWorker().fetch(
    new Request(buildingManagementBridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(payload.available, true);
  assert.equal(payload.target_building.native_feature_id, "lt_c_spbd.7");
  assert.equal(payload.target_building.bd_mgt_sn, buildingManagementNumber);
  assert.equal(payload.receipt.target_native_feature_id, "lt_c_spbd.7");
  assert.equal(payload.receipt.target_bd_mgt_sn, buildingManagementNumber);
});

test("rejects a native request that only equals the building management number", async () => {
  const crossKindUrl = bridgeUrl.replace("target_identifier_value=lt_c_spbd.7", `target_identifier_value=${buildingManagementNumber}`);
  const response = await makeWorker().fetch(
    new Request(crossKindUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(payload.available, false);
  assert.equal(payload.reason, "canyon_target_identifier_mismatch");
  assert.equal(payload.facade_gap_m, null);
  assert.equal(payload.selection_id, selectionId);
});

test("reports a missing native feature ID without falling back to the building management number", async () => {
  const missingNative = structuredClone(buildingFeatures);
  delete missingNative.features[0].id;
  const response = await makeWorker(missingNative).fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(payload.available, false);
  assert.equal(payload.reason, "canyon_target_identifier_missing");
  assert.equal(payload.facade_gap_m, null);
  assert.equal(payload.selection_id, selectionId);
});

test("keeps dual-identifier canyon receipts stable when official feature order reverses", async () => {
  const reversed = structuredClone(buildingFeatures);
  reversed.features.reverse();
  const forwardResponse = await makeWorker().fetch(
    new Request(bridgeUrl, { headers: { authorization: "Bearer server-only-token" } }),
    env,
  );
  const reversedResponse = await makeWorker(reversed).fetch(
    new Request(bridgeUrl, { headers: { authorization: "Bearer server-only-token" } }),
    env,
  );
  const forward = await forwardResponse.json();
  const backward = await reversedResponse.json();

  assert.equal(forward.available, true);
  assert.equal(backward.available, true);
  assert.equal(forward.target_building.native_feature_id, backward.target_building.native_feature_id);
  assert.equal(forward.target_building.bd_mgt_sn, backward.target_building.bd_mgt_sn);
  assert.equal(forward.opposing_building.id, backward.opposing_building.id);
  assert.deepEqual(forward.receipt.receipt_ids, backward.receipt.receipt_ids);
});

test("identifies the official upstream that is unavailable without fabricating canyon evidence", async () => {
  const worker = createWorker({
    fetchImpl: async (url) => {
      const typeName = new URL(url).searchParams.get("TYPENAME") ?? new URL(url).searchParams.get("typename");
      return new Response("unavailable", { status: typeName === "lt_c_spbd" ? 502 : 200 });
    },
  });
  const response = await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(payload.available, false);
  assert.equal(payload.source, "official_canyon_width_unavailable");
  assert.equal(payload.reason, "building_upstream_status_502");
  assert.deepEqual(payload.upstream_attempts, [
    { source_origin: "vworld_map_wfs", outcome: "upstream_status_502" },
    { source_origin: "vworld_api_wfs", outcome: "upstream_status_502" },
  ]);
});

test("classifies a VWorld invalid-key service exception without exposing its response", async () => {
  const invalidKeyBody = `<?xml version="1.0"?><ServiceExceptionReport><ServiceException code="INVALID_KEY">등록되지 않은 인증키입니다.</ServiceException></ServiceExceptionReport>`;
  const worker = createWorker({
    fetchImpl: async () => new Response(invalidKeyBody, {
      status: 200,
      headers: { "content-type": "application/xml" },
    }),
  });

  const response = await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(payload.available, false);
  assert.equal(payload.reason, "building_key_unregistered");
  assert.deepEqual(payload.upstream_attempts, [
    { source_origin: "vworld_map_wfs", outcome: "key_unregistered" },
    { source_origin: "vworld_api_wfs", outcome: "key_unregistered" },
  ]);
  assert.equal(JSON.stringify(payload).includes("등록되지 않은 인증키"), false);
});

test("does not classify marker text inside valid GeoJSON as an authentication failure", async () => {
  const validGeoJson = structuredClone(buildingFeatures);
  validGeoJson.features[0].properties.buld_nm = "INVALID_KEY PLAZA";
  const worker = createWorker({
    fetchImpl: async () => Response.json(validGeoJson),
  });

  const response = await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.notEqual(payload.reason, "building_key_unregistered");
  assert.equal(
    (payload.upstream_attempts ?? []).some(({ outcome }) => outcome === "key_unregistered"),
    false,
  );
});

test("classifies a structured VWorld JSON key error without exposing its message", async () => {
  const worker = createWorker({
    fetchImpl: async () => Response.json({
      error: { code: "INVALID_KEY", message: "secret upstream detail" },
    }),
  });

  const response = await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(payload.available, false);
  assert.equal(payload.reason, "building_key_unregistered");
  assert.equal(JSON.stringify(payload).includes("secret upstream detail"), false);
});

test("does not substring-match an unrelated structured VWorld error code", async () => {
  const worker = createWorker({
    fetchImpl: async () => Response.json({ error: { code: "NOT_INVALID_KEY" } }),
  });

  const response = await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.notEqual(payload.reason, "building_key_unregistered");
});

test("redacts arbitrary transport exception messages", async () => {
  const worker = createWorker({
    fetchImpl: async () => {
      throw new Error("building_secret-token-must-not-escape");
    },
  });

  const response = await worker.fetch(
    new Request(bridgeUrl, {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();
  const serialized = JSON.stringify(payload);

  assert.equal(payload.available, false);
  assert.equal(serialized.includes("secret-token-must-not-escape"), false);
  assert.equal(
    payload.upstream_attempts.every(({ outcome }) => outcome === "upstream_request_failed"),
    true,
  );
});
