const assert = require("node:assert/strict");
const artifacts = require("../src/use_lllm/general/static/artifact-renderer.js");

assert.equal(
  artifacts.imageSource({ type: "image", mimeType: "image/png", data: "aGVsbG8=" }),
  "data:image/png;base64,aGVsbG8="
);
assert.equal(
  artifacts.imageSource({
    type: "artifact_image",
    url: "./api/sessions/abc-123/artifacts/deadbeef-result.png",
  }),
  "./api/sessions/abc-123/artifacts/deadbeef-result.png"
);
assert.equal(
  artifacts.imageSource({ type: "artifact_image", url: "https://attacker.invalid/image.png" }),
  null
);
assert.equal(
  artifacts.imageSource({ type: "artifact_image", url: "file:///C:/private/result.png" }),
  null
);

console.log("general artifact renderer tests passed");
