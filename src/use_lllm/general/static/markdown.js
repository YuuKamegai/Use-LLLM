(function (root, factory) {
  const api = factory(() => root.katex);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.GeneralMarkdown = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (getKatex) {
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  function inline(value) {
    return value
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  }

  function hold(fragments, html, block = false) {
    const index = fragments.length;
    fragments.push({ html, block });
    return `\u0000FRAGMENT${index}\u0000`;
  }

  function renderMath(tex, displayMode, fallbackSource) {
    const katex = getKatex?.();
    if (!katex || typeof katex.renderToString !== "function") {
      return `<span class="math-fallback">${escapeHtml(fallbackSource)}</span>`;
    }
    try {
      return katex.renderToString(tex.trim(), {
        displayMode,
        throwOnError: false,
        trust: false,
        strict: "warn",
        output: "htmlAndMathml",
        maxExpand: 1000,
        maxSize: 20,
      });
    } catch (_error) {
      return `<span class="math-fallback">${escapeHtml(fallbackSource)}</span>`;
    }
  }

  function renderMarkdown(source) {
    const fragments = [];
    let held = String(source ?? "").replace(/```([^\n]*)\n?([\s\S]*?)```/g, (_all, lang, code) => {
      const block = `<div class="code-block"><button type="button" class="copy-code">コピー</button><pre><code data-language="${escapeHtml(lang.trim())}">${escapeHtml(code.replace(/^\n|\n$/g, ""))}</code></pre></div>`;
      return hold(fragments, block, true);
    });
    held = held
      .replace(/\$\$([\s\S]*?)\$\$/g, (all, tex) => (
        tex.trim() ? hold(fragments, renderMath(tex, true, all), true) : all
      ))
      .replace(/\\\[([\s\S]*?)\\\]/g, (all, tex) => (
        tex.trim() ? hold(fragments, renderMath(tex, true, all), true) : all
      ))
      .replace(/\\\(([^\n]*?)\\\)/g, (all, tex) => (
        tex.trim() ? hold(fragments, renderMath(tex, false, all)) : all
      ));
    const escaped = escapeHtml(held);
    const lines = escaped.split(/\r?\n/);
    const out = [];
    let list = false;
    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
      const raw = lines[lineIndex];
      if (raw.includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[lineIndex + 1] || "")) {
        if (list) { out.push("</ul>"); list = false; }
        const cells = (line) => line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
        const headers = cells(raw); lineIndex += 1;
        const rows = [];
        while (lines[lineIndex + 1]?.includes("|")) { lineIndex += 1; rows.push(cells(lines[lineIndex])); }
        out.push(`<table><thead><tr>${headers.map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
        continue;
      }
      const fragment = raw.match(/^\u0000FRAGMENT(\d+)\u0000$/);
      if (fragment && fragments[Number(fragment[1])]?.block) {
        if (list) { out.push("</ul>"); list = false; }
        out.push(raw);
        continue;
      }
      const heading = raw.match(/^(#{1,4})\s+(.+)$/);
      const bullet = raw.match(/^[-*]\s+(.+)$/);
      if (heading) { if (list) { out.push("</ul>"); list = false; } const level = heading[1].length; out.push(`<h${level}>${inline(heading[2])}</h${level}>`); }
      else if (bullet) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(bullet[1])}</li>`); }
      else { if (list) { out.push("</ul>"); list = false; } if (raw) out.push(`<p>${inline(raw)}</p>`); }
    }
    if (list) out.push("</ul>");
    return out.join("").replace(/\u0000FRAGMENT(\d+)\u0000/g, (_all, index) => (
      fragments[Number(index)]?.html ?? ""
    ));
  }
  return { escapeHtml, renderMarkdown };
});
