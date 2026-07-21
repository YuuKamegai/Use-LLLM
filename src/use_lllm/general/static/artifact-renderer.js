(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.GeneralArtifacts = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function append(container, item) {
    const metadata = item.metadata || {};
    const blocks = metadata.content_blocks || [];
    for (const block of blocks) {
      if (block.type === "image" && block.data) {
        const requested = String(block.mimeType || block.mime_type || "image/png").toLowerCase();
        const mime = ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(requested) ? requested : "image/png";
        const image = document.createElement("img");
        image.className = "tool-image";
        image.alt = block.alt || "MCP tool image";
        image.src = `data:${mime};base64,${block.data}`;
        container.append(image);
      } else if (block.type === "resource_link" || block.type === "resourceLink") {
        const uri = String(block.uri || "");
        const link = document.createElement(/^https?:\/\//i.test(uri) ? "a" : "span");
        link.className = "resource-link"; link.textContent = block.name || uri || "MCP resource";
        if (link.tagName === "A") { link.href = uri; link.target = "_blank"; link.rel = "noreferrer"; }
        container.append(link);
      }
    }
    if (metadata.structured_content) {
      const details = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = "構造化データ";
      const pre = document.createElement("pre"); pre.textContent = JSON.stringify(metadata.structured_content, null, 2);
      details.append(summary, pre); container.append(details);
    }
  }
  return { append };
});
