(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.GeneralMarkdown = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
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

  function renderMarkdown(source) {
    const escaped = escapeHtml(source);
    const blocks = [];
    const held = escaped.replace(/```([^\n]*)\n?([\s\S]*?)```/g, (_all, lang, code) => {
      const index = blocks.length;
      blocks.push(`<div class="code-block"><button type="button" class="copy-code">コピー</button><pre><code data-language="${lang.trim()}">${code.replace(/^\n|\n$/g, "")}</code></pre></div>`);
      return `@@CODE${index}@@`;
    });
    const lines = held.split(/\r?\n/);
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
      const code = raw.match(/^@@CODE(\d+)@@$/);
      if (code) { if (list) { out.push("</ul>"); list = false; } out.push(blocks[Number(code[1])]); continue; }
      const heading = raw.match(/^(#{1,4})\s+(.+)$/);
      const bullet = raw.match(/^[-*]\s+(.+)$/);
      if (heading) { if (list) { out.push("</ul>"); list = false; } const level = heading[1].length; out.push(`<h${level}>${inline(heading[2])}</h${level}>`); }
      else if (bullet) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(bullet[1])}</li>`); }
      else { if (list) { out.push("</ul>"); list = false; } if (raw) out.push(`<p>${inline(raw)}</p>`); }
    }
    if (list) out.push("</ul>");
    return out.join("");
  }
  return { escapeHtml, renderMarkdown };
});
