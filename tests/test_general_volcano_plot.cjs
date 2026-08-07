const assert = require("node:assert/strict");
const helpers = require("../src/use_lllm/general/static/volcano-plot.js");

const payload = {
  plot_schema: "lipidmix.volcano.v1",
  plot_type: "scatter",
  title: "Volcano (24M vs 9w)",
  comparison: { group_a: "24M", group_b: "9w", n_a: 6, n_b: 5 },
  axes: {
    x: { label: "log2 fold change", unit: null, scale: "linear" },
    y: { label: "-log10 p", unit: null, scale: "linear" },
  },
  thresholds: { q: 0.05, log2fc: 1.0 },
  points: [
    { feature: "PC 34:1", log2fc: 1.8, neg_log10_p: 3.4, sig: "up" },
    { feature: "PE 36:2", log2fc: -2.1, neg_log10_p: 4.0, sig: "down" },
    { feature: "TG 52:3", log2fc: 0.2, neg_log10_p: 0.5, sig: "ns" },
    { feature: "TG 54:4", log2fc: -0.1, neg_log10_p: 0.3, sig: "ns" },
  ],
  selection: {
    total: 4, plotted: 4, significant_total: 2, significant_plotted: 2,
    ns_total: 2, ns_plotted: 2, max_points: 3000, dropped_nonfinite: 0,
  },
  render_hints: {
    mode: "markers", color_by: "sig", show_legend: true,
    guides: { x: [-1.0, 1.0], y: [1.301] },
    hover_fields: ["feature", "log2fc", "neg_log10_p", "sig"],
  },
  caveats: [],
};

// --- findPlot: 生 JSON とフェンス JSON の両方 ---
const plot = helpers.findPlot(JSON.stringify(payload));
assert.ok(plot, "生 JSON から payload を見つけること");
assert.equal(plot.title, "Volcano (24M vs 9w)");
assert.equal(plot.xLabel, "log2 fold change");
assert.equal(plot.yLabel, "-log10 p");
assert.equal(plot.points.length, 4);
assert.equal(plot.selection.significant_total, 2);

const fenced = `volcano:\n\`\`\`json\n${JSON.stringify(payload)}\n\`\`\``;
assert.equal(helpers.findPlot(fenced).points.length, 4);

const nested = helpers.findPlot(JSON.stringify({ result: { data: payload } }));
assert.ok(nested, "入れ子から payload を見つけること");

// --- traces: sig ごとに3トレース、ns を下層に ---
const traces = helpers.traces(plot);
assert.equal(traces.length, 3);
assert.equal(traces[0].name, "ns (2)");
assert.equal(traces[1].name, "down (1)");
assert.equal(traces[2].name, "up (1)");
assert.equal(traces[0].marker.color, "#95a5a6");
assert.equal(traces[1].marker.color, "#2471a3");
assert.equal(traces[2].marker.color, "#c0392b");
assert.deepEqual(traces[2].x, [1.8]);
assert.deepEqual(traces[2].text, ["PC 34:1"]);
assert.equal(traces[2].mode, "markers");

// --- 空の sig グループはトレースを作らない ---
const upOnly = helpers.findPlot(JSON.stringify({
  ...payload,
  points: [{ feature: "PC 34:1", log2fc: 1.8, neg_log10_p: 3.4, sig: "up" }],
}));
assert.equal(helpers.traces(upOnly).length, 1);
assert.equal(helpers.traces(upOnly)[0].name, "up (1)");

// --- shapes: しきい値の破線 ---
const shapes = helpers.shapes(plot);
assert.equal(shapes.length, 3);
assert.equal(shapes.filter((item) => item.yref === "paper").length, 2);
assert.equal(shapes.filter((item) => item.xref === "paper").length, 1);
assert.equal(shapes[0].line.dash, "dash");

const noGuides = helpers.findPlot(JSON.stringify({
  ...payload, render_hints: { ...payload.render_hints, guides: undefined },
}));
assert.deepEqual(helpers.shapes(noGuides), []);

// --- 非有限値・不正スキーマ ---
assert.equal(helpers.findPlot(JSON.stringify({
  ...payload, plot_schema: "lipidmix.volcano.v99",
})), null);
assert.equal(helpers.findPlot(JSON.stringify({
  ...payload, plot_type: "line",
})), null);
assert.equal(helpers.findPlot(JSON.stringify({ ...payload, points: [] })), null);
assert.equal(helpers.findPlot("ただのテキスト応答です"), null);

const withBad = helpers.findPlot(JSON.stringify({
  ...payload,
  points: [
    { feature: "ok", log2fc: 1.8, neg_log10_p: 3.4, sig: "up" },
    { feature: "bad", log2fc: null, neg_log10_p: 3.4, sig: "ns" },
  ],
}));
assert.equal(withBad.points.length, 1, "描画できない点は落とすこと");

// --- 未知の sig は ns として扱う ---
const oddSig = helpers.findPlot(JSON.stringify({
  ...payload,
  points: [{ feature: "x", log2fc: 0.1, neg_log10_p: 0.2, sig: "maybe" }],
}));
assert.equal(oddSig.points[0].sig, "ns");

console.log("general volcano plot helper tests passed");
