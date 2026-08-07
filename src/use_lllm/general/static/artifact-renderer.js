(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.GeneralArtifacts = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const SAFE_ARTIFACT_IMAGE_URL = /^\.\/api\/sessions\/[A-Za-z0-9-]+\/artifacts\/[^/?#]+$/;

  function imageSource(block) {
    if (block.type === "image" && block.data) {
      const requested = String(block.mimeType || block.mime_type || "image/png").toLowerCase();
      const mime = ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(requested) ? requested : "image/png";
      return `data:${mime};base64,${block.data}`;
    }
    if (block.type === "artifact_image") {
      const url = String(block.url || "");
      return SAFE_ARTIFACT_IMAGE_URL.test(url) ? url : null;
    }
    return null;
  }

  function append(container, item) {
    const metadata = item.metadata || {};
    const blocks = metadata.content_blocks || [];
    for (const block of blocks) {
      const source = imageSource(block);
      if (source) {
        const image = document.createElement("img");
        image.className = "tool-image";
        image.alt = block.alt || "MCP tool image";
        image.loading = "lazy";
        image.src = source;
        if (block.type === "artifact_image") {
          const link = document.createElement("a");
          link.className = "tool-image-link";
          link.href = source;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.title = "画像を原寸で開く";
          link.append(image);
          container.append(link);
        } else {
          container.append(image);
        }
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
  return { append, imageSource };
});
