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

// --- lipidmix.eic.multi.v1（複数物質×1サンプルのオーバーレイ） ---
const multiPayload = {
  plot_schema: "lipidmix.eic.multi.v1",
  plot_type: "line",
  title: "EIC overlay | 2 compounds | liver-01",
  axes: {
    x: { label: "RT", unit: "min", scale: "linear" },
    y: { label: "Intensity", unit: null, scale: "linear" },
  },
  sample: { file_id: 3, sample_name: "liver-01", class_id: "24M" },
  series: [
    {
      id: "spot-11", label: "PC 34:1", spot_id: 11, name: "PC 34:1",
      ontology: "PC", adduct: "[M+H]+", mz: 760.5851, rt: 6.42,
      x: [6.3, 6.42, 6.5], y: [100, 900, 120],
      peak_left: 6.3, peak_top: 6.42, peak_right: 6.5,
      max_intensity: 900, mean_intensity: 373, point_count: 3,
      annotation: { text: "PC 34:1 / 6.420", x: 6.42, y: 900 },
    },
    {
      id: "spot-12", label: "PE 36:2", spot_id: 12, name: "PE 36:2",
      ontology: "PE", adduct: "[M+H]+", mz: 744.5538, rt: 7.10,
      x: [7.0, 7.1, 7.2], y: [50, 400, 60],
      peak_left: 7.0, peak_top: 7.1, peak_right: 7.2,
      max_intensity: 400, mean_intensity: 170, point_count: 3,
      annotation: { text: "PE 36:2 / 7.100", x: 7.1, y: 400 },
    },
  ],
  render_hints: {
    mode: "lines", connect_points: true, show_legend: true,
    show_annotations: true, hover_fields: ["label", "spot_id"],
  },
  caveats: [],
};

const multi = helpers.findPlot(JSON.stringify(multiPayload));
assert.ok(multi, "multi スキーマが findPlot を通ること");
assert.equal(multi.schema, "multi");
assert.equal(multi.series.length, 2);
assert.equal(multi.xLabel, "RT (min)");
assert.equal(multi.series[0].spot_id, 11);

const multiTraces = helpers.traces(multi);
assert.equal(multiTraces.length, 2);
assert.equal(multiTraces[0].customdata[0].spot_id, 11);
assert.equal(multiTraces[0].customdata[0].ontology, "PC");
assert.equal(multiTraces[0].customdata[0].mz, 760.5851);
assert.equal(multiTraces[0].customdata[0].rt, 6.42);
assert.match(multiTraces[0].hovertemplate, /760\.5851/);
assert.match(multiTraces[0].hovertemplate, /PC/);

const multiAnnotations = helpers.annotations(multi);
assert.equal(multiAnnotations.length, 2);
assert.equal(multiAnnotations[0].text, "PC 34:1 / 6.420");
assert.equal(multiAnnotations[0].x, 6.42);
assert.equal(multiAnnotations[0].showarrow, false);

const multiOff = helpers.findPlot(JSON.stringify({
  ...multiPayload,
  render_hints: { ...multiPayload.render_hints, show_annotations: false },
}));
assert.equal(helpers.annotations(multiOff).length, 0);

// single スキーマは schema フラグが立ち、注釈を返さない
assert.equal(plot.schema, "single");
assert.equal(helpers.annotations(plot).length, 0);
assert.equal(traces[0].customdata[0].sample_name, undefined);

// 未知スキーマは拒否する
assert.equal(helpers.findPlot(JSON.stringify({
  ...multiPayload, plot_schema: "lipidmix.eic.v99",
})), null);

console.log("general EIC plot helper tests passed");
