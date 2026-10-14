// Runs inside the downloaded document. All resources are embedded in that file.
(async function () {
  const session = JSON.parse(document.getElementById("session-data").textContent);
  const assets = JSON.parse(document.getElementById("session-assets").textContent);
  GeneralI18n.setLanguage(document.documentElement.lang, false);
  const t = GeneralI18n.t;
  document.getElementById("raw-session").textContent = JSON.stringify(session, null, 2);
  for (const message of session.messages) {
    const article = document.createElement("article"); article.className = `message ${message.role}`;
    const bubble = document.createElement("div"); bubble.className = "bubble";
    const heading = document.createElement("strong");
    heading.textContent = `${message.role} · ${message.metadata?.tool_name || message.created_at || ""}`;
    const content = document.createElement("div"); content.className = "message-content";
    if (message.role === "assistant") content.innerHTML = GeneralMarkdown.renderMarkdown(message.content);
    else content.textContent = message.content;
    bubble.append(heading, content); article.append(bubble);
    document.getElementById("chat-messages").append(article);
    const copy = JSON.parse(JSON.stringify(message));
    for (const block of copy.metadata?.content_blocks || []) {
      if (assets[block.url]) {
        const uri = assets[block.url];
        block.type = "image"; block.mimeType = uri.slice(5, uri.indexOf(";"));
        block.data = uri.slice(uri.indexOf(",") + 1);
      }
    }
    GeneralArtifacts.append(bubble, copy);
    if (message.role !== "tool" || message.metadata?.is_error) continue;
    for (const [helper, kind] of [[GeneralPcaPlot, "pca"], [GeneralEicPlot, "eic"], [GeneralVolcanoPlot, "volcano"]]) {
      const plot = helper.findPlot(message.content);
      if (!plot) continue;
      const card = document.createElement("section"); card.className = "chat-plot-card";
      const title = document.createElement("strong"); title.textContent = plot.title;
      card.append(title);
      if (helper.note) { const note = document.createElement("p"); note.textContent = helper.note(plot); card.append(note); }
      const target = document.createElement("div"); target.className = "chat-pca-plot";
      card.append(target); bubble.append(card);
      try {
        await Plotly.newPlot(target, helper.traces(kind === "pca" ? plot.points : plot), {
          xaxis: { title: plot.xLabel }, yaxis: { title: plot.yLabel },
          shapes: helper.shapes?.(plot) || [], annotations: helper.annotations?.(plot) || [],
          margin: { l: 65, r: 35, t: 25, b: 70 }, legend: { orientation: "h" },
        }, { responsive: true, displaylogo: false });
      } catch (error) {
        target.textContent = `${t("グラフを描画できませんでした。元データは全データ欄に保存されています。")} ${error.message}`;
      }
    }
  }
})();
