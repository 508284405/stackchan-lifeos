import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";

const source = readFileSync(new URL("./src/DeviceControls.jsx", import.meta.url), "utf8");
const distAsset = readdirSync(new URL("./dist/assets/", import.meta.url)).find((name) => /^index-.*\.js$/.test(name));
assert.ok(distAsset, "built JavaScript asset exists");
const dist = readFileSync(new URL(`./dist/assets/${distAsset}`, import.meta.url), "utf8");

for (const direction of ["manual.up", "manual.left", "manual.right", "manual.down"]) {
  assert.match(source, new RegExp(`t\\("${direction}"\\)`), `${direction} is rendered from i18n`);
  assert.match(dist, new RegExp(direction.replace(".", "\\.")), `${direction} is present in dist`);
}

assert.match(source, /manual_control_v1/);
assert.match(source, /ttl_ms: 500/);
assert.match(source, /manual\.up"\), \{ yaw: 0, pitch: 1 \}/);
assert.match(source, /manual\.left"\), \{ yaw: 1, pitch: 0 \}/);
assert.match(source, /manual\.right"\), \{ yaw: -1, pitch: 0 \}/);
assert.match(source, /manual\.down"\), \{ yaw: 0, pitch: -1 \}/);
assert.match(source, /const safeIdle = safetyClear && health\.torque_enabled === false/);
assert.match(source, /const manualReady = online && controlEnabled && manualEnabled/);
assert.match(source, /event\.key === "Enter" \|\| event\.key === " "/);
assert.match(source, /onPointerDown=/);
assert.match(source, /onPointerUp={release}/);

console.log("manual control bar regression checks passed");
