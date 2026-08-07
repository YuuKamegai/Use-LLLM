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

  // 1物質×複数サンプル（single）と複数物質×1サンプル（multi）の2スキーマを受ける。
  const SCHEMAS = { "lipidmix.eic.v1": "single", "lipidmix.eic.multi.v1": "multi" };

  function normalizePlot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const schema = SCHEMAS[value.plot_schema];
    if (!schema || value.plot_type !== "line") return null;
    if (!Array.isArray(value.series)) return null;
    const series = value.series.map(normalizeSeries).filter(Boolean);
    if (!series.length || series.length !== value.series.length) return null;
    return {
      schema,
      title: value.title == null || value.title === "" ? "Extracted ion chromatogram" : String(value.title),
      xLabel: axisLabel(value.axes?.x, "RT"),
      yLabel: axisLabel(value.axes?.y, "Intensity"),
      showAnnotations: value.render_hints?.show_annotations === true,
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

  function customdataFor(plot, item) {
    if (plot.schema === "multi") {
      return {
        spot_id: item.spot_id,
        name: item.name,
        ontology: item.ontology,
        adduct: item.adduct,
        mz: item.mz,
        rt: item.rt,
        peak_top: item.peak_top,
        max_intensity: item.max_intensity,
      };
    }
    return {
      file_id: item.file_id,
      sample_name: item.sample_name,
      class_id: item.class_id,
      peak_left: item.peak_left,
      peak_top: item.peak_top,
      peak_right: item.peak_right,
    };
  }

  // multi の物質メタは trace 内で一定なので、customdata 補間に頼らず
  // hovertemplate 文字列へ焼き込む（Plotly のバージョン差で崩れないため）。
  function hovertemplateFor(plot, item) {
    const tail = "x %{x:.5g}<br>intensity %{y:.5g}<extra></extra>";
    if (plot.schema !== "multi") return `%{fullData.name}<br>${tail}`;
    const mz = Number(item.mz);
    const rt = Number(item.rt);
    const meta = [
      item.ontology ? String(item.ontology) : null,
      Number.isFinite(mz) ? `m/z ${mz.toFixed(4)}` : null,
      Number.isFinite(rt) ? `RT ${rt.toFixed(2)}` : null,
    ].filter(Boolean).join(" · ");
    return meta
      ? `%{fullData.name}<br>${meta}<br>${tail}`
      : `%{fullData.name}<br>${tail}`;
  }

  function traces(plot) {
    return plot.series.map((item) => ({
      type: item.x.length > 500 ? "scattergl" : "scatter",
      mode: "lines",
      name: item.label,
      x: item.x,
      y: item.y,
      customdata: item.x.map(() => customdataFor(plot, item)),
      hovertemplate: hovertemplateFor(plot, item),
      line: { width: 1.5 },
    }));
  }

  function annotations(plot) {
    if (plot.schema !== "multi" || !plot.showAnnotations) return [];
    return plot.series
      .filter((item) => item.annotation && Number.isFinite(Number(item.annotation.x)))
      .map((item) => ({
        text: String(item.annotation.text == null ? item.label : item.annotation.text),
        x: Number(item.annotation.x),
        y: Number(item.annotation.y),
        showarrow: false,
        textangle: -45,
        yshift: 10,
        xanchor: "left",
        font: { size: 9 },
      }));
  }

  return { findPlot, traces, annotations };
}));
