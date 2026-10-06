const assert = require("node:assert/strict");
const frame = require("../src/use_lllm/general/static/resizable-frame.js");

assert.equal(frame.nextHeight(300, 50, 80, 2400), 350);
assert.equal(frame.nextHeight(300, -400, 80, 2400), 80);
assert.equal(frame.nextHeight(300, 5000, 80, 2400), 2400);
assert.equal(frame.nextHeight(300.4, 0.4, 80, 2400), 301);
assert.equal(frame.nextHeight(300, 0, 240, 100), 240);

console.log("general resizable frame tests passed");
