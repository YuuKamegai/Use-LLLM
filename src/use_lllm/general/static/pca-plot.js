(function exposePcaPlot(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.GeneralPcaPlot = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function createPcaPlotHelpers() {
  "use strict";

  function parseJsonCandidates(value) {
    if (typeof value !== "string") return [value];
    const candidates = [];
    try { candidates.push(JSON.parse(value)); } catch (_) { /* Markdown response */ }
    const fences = /```(?:json)?\s*([\s\S]*?)```/gi;
    let match;
    while ((match = fences.exec(value)) !== null) {
      try { candidates.push(JSON.parse(match[1].trim())); } catch (_) { /* Ignore non-JSON fences. */ }
    }
    return candidates;
  }

  function valueFor(row, candidates) {
    const lookup = new Map(Object.keys(row).map((key) => [key.toLowerCase(), row[key]]));
    for (const candidate of candidates) {
      if (lookup.has(candidate)) return lookup.get(candidate);
    }
    return undefined;
  }

  function normalizeRow(row, index, allowCartesian = false) {
    if (!row || typeof row !== "object" || Array.isArray(row)) return null;
    const rawX = valueFor(row, allowCartesian ? ["pc1", "x"] : ["pc1"]);
    const rawY = valueFor(row, allowCartesian ? ["pc2", "y"] : ["pc2"]);
    const x = Number(rawX);
    const y = Number(rawY);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    const label = valueFor(row, ["sample", "sample_name", "name", "id", "label"]);
    const group = valueFor(row, ["group", "class_id", "class", "category"]);
    return {
      x,
      y,
      label: label == null || label === "" ? `sample ${index + 1}` : String(label),
      group: group == null || group === "" ? "samples" : String(group),
      raw: row,
    };
  }

  function isPcaAxisPair(xLabel, yLabel) {
    return /^pc\s*1\b/i.test(String(xLabel || "").trim())
      && /^pc\s*2\b/i.test(String(yLabel || "").trim());
  }

  function varianceLabel(component, value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return component;
    const percent = Math.abs(number) <= 1 ? number * 100 : number;
    return `${component} (${percent.toFixed(2)}%)`;
  }

  function metadataFor(value, inherited = {}) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return inherited;
    const explicitXLabel = valueFor(value, ["x_label", "xlabel"]);
    const explicitYLabel = valueFor(value, ["y_label", "ylabel"]);
    return {
      title: valueFor(value, ["title"]) || inherited.title || "PCA plot",
      xLabel: explicitXLabel || inherited.xLabel || "PC1",
      yLabel: explicitYLabel || inherited.yLabel || "PC2",
      pcaAxes: isPcaAxisPair(explicitXLabel, explicitYLabel) || Boolean(inherited.pcaAxes),
    };
  }

  function plotFromComponents(value, metadata) {
    const components = valueFor(value, ["components"]);
    const variance = valueFor(value, ["explained_variance_ratio"]);
    if (!Array.isArray(components) || !Array.isArray(variance) || variance.length < 2) return null;
    const names = valueFor(value, ["sample_names", "samples"]);
    const groups = valueFor(value, ["groups"]);
    const points = components.map((coordinate, index) => {
      if (!Array.isArray(coordinate) || coordinate.length < 2) return null;
      const x = Number(coordinate[0]);
      const y = Number(coordinate[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      const label = Array.isArray(names) ? names[index] : null;
      const group = label != null && groups && typeof groups === "object" ? groups[label] : null;
      return {
        x,
        y,
        label: label == null || label === "" ? `sample ${index + 1}` : String(label),
        group: group == null || group === "" ? "samples" : String(group),
        raw: { component: coordinate, sample: label, group },
      };
    }).filter(Boolean);
    if (!points.length) return null;
    return {
      ...metadata,
      xLabel: varianceLabel("PC1", variance[0]),
      yLabel: varianceLabel("PC2", variance[1]),
      points,
    };
  }

  function findPlotInValue(value, inherited = {}) {
    if (typeof value === "string") {
      for (const candidate of parseJsonCandidates(value)) {
        if (candidate === value) continue;
        const nested = findPlotInValue(candidate, inherited);
        if (nested) return nested;
      }
      return null;
    }
    if (!value || typeof value !== "object") return null;
    const metadata = metadataFor(value, inherited);
    if (Array.isArray(value)) {
      const points = value.map((row, index) => normalizeRow(row, index)).filter(Boolean);
      if (points.length) return { ...metadata, points };
      for (const child of value) {
        const nested = findPlotInValue(child, metadata);
        if (nested) return nested;
      }
      return null;
    }

    const componentPlot = plotFromComponents(value, metadata);
    if (componentPlot) return componentPlot;

    const preferred = ["scores", "pca_scores", "coordinates", "points", "samples"];
    for (const key of preferred) {
      if (Object.hasOwn(value, key)) {
        const child = value[key];
        if (Array.isArray(child)) {
          const allowCartesian = metadata.pcaAxes;
          const points = child
            .map((row, index) => normalizeRow(row, index, allowCartesian))
            .filter(Boolean);
          if (points.length) return { ...metadata, points };
        }
        const nested = findPlotInValue(child, metadata);
        if (nested) return nested;
      }
    }
    for (const child of Object.values(value)) {
      const nested = findPlotInValue(child, metadata);
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

  function findPoints(input) {
    return findPlot(input)?.points || null;
  }

  function traces(points) {
    const groups = new Map();
    for (const point of points) {
      if (!groups.has(point.group)) groups.set(point.group, []);
      groups.get(point.group).push(point);
    }
    return [...groups.entries()].map(([name, rows]) => ({
      type: "scatter",
      mode: "markers",
      name,
      x: rows.map((row) => row.x),
      y: rows.map((row) => row.y),
      text: rows.map((row) => row.label),
      customdata: rows.map((row) => row.raw),
      hovertemplate: "%{text}<br>PC1 %{x:.4g}<br>PC2 %{y:.4g}<extra>%{fullData.name}</extra>",
      marker: { size: 9, opacity: 0.82, line: { color: "#ffffff", width: 1 } },
    }));
  }

  return { findPlot, findPoints, traces };
}));
