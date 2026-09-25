import test from "node:test";
import assert from "node:assert/strict";
import { diagramModel, modelName } from "../src/diagramModel.ts";

test("a recorded run keeps its own decision model", () => {
  assert.equal(diagramModel({ decision_provider: "laya" }, "jev"), "laya");
  assert.equal(diagramModel({ decision_provider: "jev" }, "laya"), "jev");
  assert.equal(diagramModel({ goal: "old" }, "laya"), "jev");
});

test("the composer choice labels the diagram before a run exists", () => {
  assert.equal(diagramModel(null, "laya"), "laya");
  assert.equal(diagramModel(undefined, "jev"), "jev");
  assert.equal(modelName("laya"), "Laya");
  assert.equal(modelName("jev"), "Jev");
});
