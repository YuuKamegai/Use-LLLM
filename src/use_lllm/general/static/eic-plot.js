(function exposeEicPlot(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.GeneralEicPlot = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function createEicPlotHelpers() {
  "use strict";

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

  function normalizeSeries(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (!Array.isArray(value.x) || !Array.isArray(value.y) || value.x.length !== value.y.length) return null;
    if (!value.x.length) return null;
    const x = value.x.map(Number);
    const y = value.y.map(Number);
    if (x.some((item) => !Number.isFinite(item)) || y.some((item) => !Number.isFinite(item))) return null;
    return {
      ...value,
      id: value.id == null ? "" : String(value.id),
      label: value.label == null || value.label === "" ? "EIC trace" : String(value.label),
      x,
      y,
    };
  }

  function normalizePlot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (value.plot_schema !== "lipidmix.eic.v1" || value.plot_type !== "line") return null;
    if (!Array.isArray(value.series)) return null;
    const series = value.series.map(normalizeSeries).filter(Boolean);
    if (!series.length || series.length !== value.series.length) return null;
    return {
      title: value.title == null || value.title === "" ? "Extracted ion chromatogram" : String(value.title),
      xLabel: axisLabel(value.axes?.x, "RT"),
      yLabel: axisLabel(value.axes?.y, "Intensity"),
      series,
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
    return plot.series.map((item) => ({
      type: item.x.length > 500 ? "scattergl" : "scatter",
      mode: "lines",
      name: item.label,
      x: item.x,
      y: item.y,
      customdata: item.x.map(() => ({
        file_id: item.file_id,
        sample_name: item.sample_name,
        class_id: item.class_id,
        peak_left: item.peak_left,
        peak_top: item.peak_top,
        peak_right: item.peak_right,
      })),
      hovertemplate: "%{fullData.name}<br>x %{x:.5g}<br>intensity %{y:.5g}<extra></extra>",
      line: { width: 1.5 },
    }));
  }

  return { findPlot, traces };
}));
