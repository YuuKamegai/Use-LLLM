(function exposeVolcanoPlot(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.GeneralVolcanoPlot = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function createVolcanoPlotHelpers() {
  "use strict";

  const SCHEMA = "lipidmix.volcano.v1";

  // 配色は save_volcano_figure（matplotlib）と一致させ、PNG と画面で色が
  // 入れ替わらないようにする。ns を先に積んで有意点を上に描く。
  const GROUPS = [
    { sig: "ns", color: "#95a5a6", size: 4, opacity: 0.45 },
    { sig: "down", color: "#2471a3", size: 6, opacity: 0.85 },
    { sig: "up", color: "#c0392b", size: 6, opacity: 0.85 },
  ];

  function parseJsonCandidates(value) {
    if (typeof value !== "string") return [value];
    const candidates = [];
    try { candidates.push(JSON.parse(value)); } catch (_) { /* Markdown response */ }
    const fences = /```(?:json)?\s*([\s\S]*?)```/gi;
    let match;
    while ((match = fences.exec(value)) !== null) {
      try { candidates.push(JSON.parse(match[1].trim())); } catch (_) { /* Ignore invalid fences. */ }
    }
    return candidates;
  }

  function axisLabel(axis, fallback) {
    if (!axis || typeof axis !== "object") return fallback;
    const label = axis.label == null || axis.label === "" ? fallback : String(axis.label);
    return axis.unit == null || axis.unit === "" ? label : `${label} (${axis.unit})`;
  }

  // Number(null) は 0、Number("") も 0 になる。サーバ側で log2fc=None の点は
  // 既に除かれるが、素の Number() では欠損が原点の点として紛れ込むため弾く。
  function coerceNumber(value) {
    if (value == null || value === "" || typeof value === "boolean") return NaN;
    return Number(value);
  }

  function normalizePoint(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const x = coerceNumber(value.log2fc);
    const y = coerceNumber(value.neg_log10_p);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return {
      x,
      y,
      feature: value.feature == null || value.feature === "" ? "(unnamed)" : String(value.feature),
      sig: value.sig === "up" || value.sig === "down" ? value.sig : "ns",
    };
  }

  function normalizePlot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (value.plot_schema !== SCHEMA || value.plot_type !== "scatter") return null;
    if (!Array.isArray(value.points)) return null;
    const points = value.points.map(normalizePoint).filter(Boolean);
    if (!points.length) return null;
    return {
      title: value.title == null || value.title === "" ? "Volcano plot" : String(value.title),
      xLabel: axisLabel(value.axes?.x, "log2 fold change"),
      yLabel: axisLabel(value.axes?.y, "-log10 p"),
      points,
      guides: value.render_hints?.guides || null,
      selection: value.selection || null,
      raw: value,
    };
  }

  function findPlotInValue(value, seen = new Set()) {
    if (typeof value === "string") {
      for (const candidate of parseJsonCandidates(value)) {
        if (candidate === value) continue;
        const nested = findPlotInValue(candidate, seen);
        if (nested) return nested;
      }
      return null;
    }
    if (!value || typeof value !== "object" || seen.has(value)) return null;
    seen.add(value);
    const direct = normalizePlot(value);
    if (direct) return direct;
    for (const child of Object.values(value)) {
      const nested = findPlotInValue(child, seen);
      if (nested) return nested;
    }
    return null;
  }

  function findPlot(input) {
    for (const candidate of parseJsonCandidates(input)) {
      const plot = findPlotInValue(candidate);
      if (plot) return plot;
    }
    return null;
  }

  function traces(plot) {
    return GROUPS.map((group) => {
      const rows = plot.points.filter((point) => point.sig === group.sig);
      if (!rows.length) return null;
      return {
        type: rows.length > 1000 ? "scattergl" : "scatter",
        mode: "markers",
        name: `${group.sig} (${rows.length})`,
        x: rows.map((row) => row.x),
        y: rows.map((row) => row.y),
        text: rows.map((row) => row.feature),
        hovertemplate: `%{text}<br>log2FC %{x:.3f}<br>-log10 p %{y:.3f}<extra>${group.sig}</extra>`,
        marker: {
          size: group.size,
          color: group.color,
          opacity: group.opacity,
          line: { width: 0 },
        },
      };
    }).filter(Boolean);
  }

  function shapes(plot) {
    const guides = plot.guides;
    if (!guides || typeof guides !== "object") return [];
    const line = { color: "#b3ada2", width: 1, dash: "dash" };
    const out = [];
    for (const value of Array.isArray(guides.x) ? guides.x : []) {
      const x = Number(value);
      if (!Number.isFinite(x)) continue;
      out.push({ type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1, line });
    }
    for (const value of Array.isArray(guides.y) ? guides.y : []) {
      const y = Number(value);
      if (!Number.isFinite(y)) continue;
      out.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: y, y1: y, line });
    }
    return out;
  }

  return { findPlot, traces, shapes };
}));
