const assert = require("node:assert/strict");
const helpers = require("../src/use_lllm/general/static/eic-plot.js");

const payload = {
  plot_schema: "lipidmix.eic.v1",
  plot_type: "line",
  title: "EIC spot 42",
  axes: {
    x: { label: "RT", unit: "min", scale: "linear" },
    y: { label: "Intensity", unit: null, scale: "linear" },
  },
  series: [
    {
      id: "file-7",
      label: "sample A",
      file_id: 7,
      class_id: "control",
      x: [1.1, 1.2, 1.3],
      y: [10, 25, 5],
      peak_left: 1.1,
      peak_top: 1.2,
      peak_right: 1.3,
    },
  ],
};

const plot = helpers.findPlot(JSON.stringify(payload));
assert.equal(plot.title, "EIC spot 42");
assert.equal(plot.xLabel, "RT (min)");
assert.equal(plot.yLabel, "Intensity");
assert.deepEqual(plot.series[0].x, [1.1, 1.2, 1.3]);

const traces = helpers.traces(plot);
assert.equal(traces.length, 1);
assert.equal(traces[0].type, "scatter");
assert.equal(traces[0].mode, "lines");
assert.equal(traces[0].customdata[0].file_id, 7);

const fenced = `EIC plot data:\n\`\`\`json\n${JSON.stringify(payload)}\n\`\`\``;
assert.equal(helpers.findPlot(fenced).series.length, 1);
assert.equal(helpers.findPlot({ result: { structuredContent: payload } }).title, "EIC spot 42");

assert.equal(helpers.findPlot({ ...payload, plot_schema: "other.v1" }), null);
assert.equal(helpers.findPlot({ ...payload, series: [{ x: [1], y: [2, 3] }] }), null);
assert.equal(helpers.findPlot("plain text"), null);

console.log("general EIC plot helper tests passed");
