const assert = require("node:assert/strict");
const helpers = require("../src/use_lllm/general/static/pca-plot.js");

const nested = {
  result: {
    pca_scores: [
      { sample_name: "A", PC1: 1.25, PC2: -0.5, class_id: "control" },
      { sample_name: "B", pc1: "2.5", pc2: 0.75, group: "treated" },
    ],
  },
};

const points = helpers.findPoints(JSON.stringify(nested));
assert.equal(points.length, 2);
assert.deepEqual(points.map((point) => point.label), ["A", "B"]);
assert.deepEqual(points.map((point) => point.group), ["control", "treated"]);
assert.deepEqual(points.map((point) => point.x), [1.25, 2.5]);

const traces = helpers.traces(points);
assert.equal(traces.length, 2);
assert.equal(traces[0].type, "scatter");
assert.equal(helpers.findPoints({ rows: [{ x: 1, y: 2 }] }), null);

const fenced = `PCAを実行しました。
\`\`\`json
{"x_label":"PC1 (28.39%)","y_label":"PC2 (20.84%)","points":[{"x":1.2,"y":-0.4,"label":"QC 1","group":"QC"},{"x":2.1,"y":0.8,"sample":"Sample A","group":"control"}]}
\`\`\``;
const fencedPlot = helpers.findPlot(fenced);
assert.equal(fencedPlot.points.length, 2);
assert.equal(fencedPlot.xLabel, "PC1 (28.39%)");
assert.equal(fencedPlot.yLabel, "PC2 (20.84%)");
assert.deepEqual(fencedPlot.points.map((point) => point.label), ["QC 1", "Sample A"]);

const componentPlot = helpers.findPlot({
  result: {
    components: [[1, 2], [-3, 4]],
    explained_variance_ratio: [0.45, 0.25],
    sample_names: ["A", "B"],
    groups: { A: "control", B: "treated" },
  },
});
assert.equal(componentPlot.xLabel, "PC1 (45.00%)");
assert.equal(componentPlot.points[1].group, "treated");

const nestedTextPlot = helpers.findPlot({ content: fenced });
assert.equal(nestedTextPlot.points.length, 2);

assert.equal(helpers.findPlot("plain text without PCA data"), null);
assert.equal(helpers.findPlot({ points: [{ x: 1, y: 2 }] }), null);

console.log("general PCA plot helper tests passed");
