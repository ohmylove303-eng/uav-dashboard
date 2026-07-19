import assert from "node:assert/strict";
import test from "node:test";

import { measureFacadeGap } from "../src/geometry.mjs";

const roadPath = [
  [0, -20],
  [0, 20],
];

const targetRing = [
  [-8, -4],
  [-4, -4],
  [-4, 4],
  [-8, 4],
  [-8, -4],
];

test("uses an opposing official building facade gap instead of road right-of-way width", () => {
  const measurement = measureFacadeGap({
    targetRing,
    roadPath,
    buildings: [
      {
        id: "opposing-building",
        name: "Opposing Building",
        ring: [
          [4, -4],
          [8, -4],
          [8, 4],
          [4, 4],
          [4, -4],
        ],
      },
    ],
  });

  assert.equal(measurement.available, true);
  assert.equal(measurement.facadeGapM, 8);
  assert.equal(measurement.opposingBuildingId, "opposing-building");
  assert.equal(measurement.roadCrossingVerified, true);
  assert.equal(measurement.normalAlignment, 1);
});

test("rejects a nearer building when it is on the same side of the road", () => {
  const measurement = measureFacadeGap({
    targetRing,
    roadPath,
    buildings: [
      {
        id: "same-side-building",
        name: "Same Side Building",
        ring: [
          [-3, -4],
          [-1, -4],
          [-1, 4],
          [-3, 4],
          [-3, -4],
        ],
      },
    ],
  });

  assert.deepEqual(measurement, {
    available: false,
    facadeGapM: null,
    opposingBuildingId: null,
    opposingBuildingName: null,
    roadCrossingVerified: false,
    normalAlignment: null,
    targetPoint: null,
    opposingPoint: null,
    reason: "opposing_official_building_not_matched",
  });
});
