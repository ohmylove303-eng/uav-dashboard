import assert from "node:assert/strict";
import test from "node:test";

import { createWorker } from "../src/index.mjs";

const buildingFeatures = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      id: "target-building",
      properties: { buld_nm: "Target Building" },
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

function makeWorker() {
  return createWorker({
    fetchImpl: async (url) => new Response(
      String(url).includes("TYPENAME=lt_l_n3a0020000") ? JSON.stringify(roadFeatures) : JSON.stringify(buildingFeatures),
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
    new Request("https://bridge.example/api/canyon-width?lat=0&lon=-0.00006", {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );

  const buildingUrl = urls.find((url) => url.searchParams.get("TYPENAME") === "lt_c_spbd");
  assert.equal(buildingUrl?.hostname, "map.vworld.kr");
  assert.equal(buildingUrl?.pathname, "/js/wfs.do");
  assert.equal(buildingUrl?.searchParams.get("SRSNAME"), "EPSG:3857");
  assert.equal(buildingUrl?.searchParams.get("APIKEY"), "vworld-server-only-key");
  assert.equal(buildingUrl?.searchParams.get("DOMAIN"), env.VWORLD_REFERER);
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
    new Request("https://bridge.example/api/canyon-width?lat=0&lon=-0.00006", {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );

  const payload = await response.json();
  const apiBuildingUrl = urls.find((url) => url.hostname === "api.vworld.kr" && url.searchParams.get("TYPENAME") === "lt_c_spbd");
  assert.equal(payload.available, true);
  assert.equal(apiBuildingUrl?.searchParams.get("key"), "vworld-server-only-key");
  assert.equal(apiBuildingUrl?.searchParams.has("DOMAIN"), false);
});

const env = {
  OFFICIAL_GIS_BRIDGE_TOKEN: "server-only-token",
  VWORLD_DATA_API_KEY: "vworld-server-only-key",
  VWORLD_REFERER: "https://uav-dashboard.onrender.com",
  VWORLD_WFS_TYPENAME: "lt_c_spbd",
};

test("requires the server-only Render authorization token", async () => {
  const response = await makeWorker().fetch(new Request("https://bridge.example/api/canyon-width?lat=0&lon=-0.00006"), env);

  assert.equal(response.status, 401);
});

test("returns a verified facade gap instead of promoting official road right-of-way as a canyon width", async () => {
  const response = await makeWorker().fetch(
    new Request("https://bridge.example/api/canyon-width?lat=0&lon=-0.00006", {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(payload.available, true);
  assert.equal(payload.official_available, true);
  assert.equal(payload.source, "official_canyon_width");
  assert.notEqual(payload.facade_gap_m, 49.7);
  assert.equal(payload.official_road_right_of_way_width_m, 49.7);
  assert.equal(payload.receipt.road_crossing_verified, true);
  assert.equal(payload.target_building.id, "target-building");
  assert.equal(payload.opposing_building.id, "opposing-building");
});

test("identifies the official upstream that is unavailable without fabricating canyon evidence", async () => {
  const worker = createWorker({
    fetchImpl: async (url) => new Response("unavailable", { status: String(url).includes("TYPENAME=lt_c_spbd") ? 502 : 200 }),
  });
  const response = await worker.fetch(
    new Request("https://bridge.example/api/canyon-width?lat=0&lon=-0.00006", {
      headers: { authorization: "Bearer server-only-token" },
    }),
    env,
  );
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(payload.available, false);
  assert.equal(payload.source, "official_canyon_width_unavailable");
  assert.equal(payload.reason, "building_upstream_status_502");
});
