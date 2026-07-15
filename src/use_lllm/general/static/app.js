const $ = (selector) => document.querySelector(selector);

const state = {
  settings: null,
  sessions: [],
  current: null,
  tools: [],
  controller: null,
};

async function api(path, options = {}) {
  const response = await fetch(`./api/${path.replace(/^\//, "")}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* no json */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

let toastTimer;
function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = ""; }, 3400);
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("ja-JP", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

async function loadSettings() {
  state.settings = await api("settings");
  renderEndpointBanner();
  renderEndpointList();
  renderServerList();
  await loadTools();
}

function selectedEndpoint() {
  return state.settings?.endpoints.find((item) => item.name === state.settings.selected_endpoint);
}

function renderEndpointBanner() {
  const endpoint = selectedEndpoint();
  const banner = $("#endpoint-banner");
  if (!endpoint) {
    banner.querySelector("strong").textContent = "未設定";
    return;
  }
  const remote = endpoint.trust === "lan_allowed";
  banner.classList.toggle("remote", remote);
  banner.querySelector("small").textContent = remote ? "LAN送信先" : "ローカル送信先";
  banner.querySelector("strong").textContent = `${endpoint.name} · ${endpoint.base_url} · ${endpoint.default_model || "モデル未設定"}`;
  banner.title = remote ? `プロンプトは ${endpoint.base_url} へ送信されます` : "このPC上のOllamaへ送信します";
}

function actionButton(label, handler, className = "mini-button") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function renderEndpointList() {
  const list = $("#endpoint-list");
  list.textContent = "";
  for (const item of state.settings.endpoints) {
    const card = document.createElement("article");
    card.className = "connection-card";
    const top = document.createElement("div");
    top.className = "connection-top";
    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.name;
    const detail = document.createElement("small");
    detail.textContent = `${item.base_url} · ${item.default_model || "モデル未設定"}`;
    info.append(title, detail);
    const chip = document.createElement("span");
    chip.className = `state-chip${item.name === state.settings.selected_endpoint ? "" : " off"}`;
    chip.textContent = item.name === state.settings.selected_endpoint ? "選択中" : item.trust;
    top.append(info, chip);
    const actions = document.createElement("div");
    actions.className = "connection-actions";
    if (item.name !== state.settings.selected_endpoint) actions.append(actionButton("選択", () => selectEndpoint(item.name)));
    actions.append(actionButton("編集", () => openEndpointForm(item)));
    actions.append(actionButton("削除", () => deleteEndpoint(item.name), "mini-button danger-text"));
    card.append(top, actions);
    list.append(card);
  }
}

function openEndpointForm(item = null) {
  const form = $("#endpoint-form");
  form.classList.remove("hidden");
  $("#endpoint-original").value = item?.name || "";
  $("#endpoint-name").value = item?.name || "";
  $("#endpoint-url").value = item?.base_url || "http://127.0.0.1:11434";
  $("#endpoint-model").value = item?.default_model || "qwen3:14b";
  $("#endpoint-trust").value = item?.trust || "loopback";
  $("#endpoint-name").focus();
}

async function saveEndpoint(event) {
  event.preventDefault();
  const original = $("#endpoint-original").value;
  const payload = {
    name: $("#endpoint-name").value.trim(),
    base_url: $("#endpoint-url").value.trim(),
    default_model: $("#endpoint-model").value.trim(),
    trust: $("#endpoint-trust").value,
  };
  try {
    state.settings = await api(original ? `endpoints/${encodeURIComponent(original)}` : "endpoints", { method: original ? "PUT" : "POST", body: JSON.stringify(payload) });
    $("#endpoint-form").classList.add("hidden");
    renderEndpointBanner(); renderEndpointList(); renderServerList(); await loadTools();
    toast("Ollamaエンドポイントを保存しました。");
  } catch (error) { toast(error.message, true); }
}

async function selectEndpoint(name) {
  try {
    state.settings = await api(`endpoints/${encodeURIComponent(name)}/select`, { method: "POST" });
    renderEndpointBanner(); renderEndpointList();
    toast(`${name} を送信先に選択しました。`);
  } catch (error) { toast(error.message, true); }
}

async function deleteEndpoint(name) {
  if (!confirm(`Ollamaエンドポイント「${name}」を削除しますか？`)) return;
  try {
    state.settings = await api(`endpoints/${encodeURIComponent(name)}`, { method: "DELETE" });
    renderEndpointBanner(); renderEndpointList();
  } catch (error) { toast(error.message, true); }
}

function serverStatus(name) {
  return state.settings.mcp_statuses.find((item) => item.name === name) || { status: "disconnected", tool_count: 0 };
}

function renderServerList() {
  const list = $("#server-list");
  list.textContent = "";
  if (!state.settings.mcp_servers.length) list.innerHTML = '<p class="muted">MCPサーバーは未登録です。</p>';
  for (const item of state.settings.mcp_servers) {
    const status = serverStatus(item.name);
    const card = document.createElement("article");
    card.className = "connection-card";
    const top = document.createElement("div");
    top.className = "connection-top";
    const info = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = item.name;
    const detail = document.createElement("small"); detail.textContent = `${item.command} ${item.args.join(" ")}`.trim();
    info.append(title, detail);
    const chip = document.createElement("span");
    chip.className = `state-chip${status.status === "connected" ? "" : " off"}`;
    chip.textContent = status.status === "connected" ? `${status.tool_count} tools` : status.status;
    top.append(info, chip);
    const actions = document.createElement("div"); actions.className = "connection-actions";
    actions.append(actionButton(status.status === "connected" ? "切断" : "接続", () => toggleServer(item.name, status.status === "connected")));
    actions.append(actionButton("編集", () => openServerForm(item)));
    actions.append(actionButton("削除", () => deleteServer(item.name), "mini-button danger-text"));
    card.append(top, actions);
    if (status.error) { const error = document.createElement("small"); error.className = "danger-text"; error.textContent = status.error; card.append(error); }
    list.append(card);
  }
}

function openServerForm(item = null) {
  $("#server-form").classList.remove("hidden");
  $("#server-original").value = item?.name || "";
  $("#server-name").value = item?.name || "";
  $("#server-command").value = item?.command || "";
  $("#server-args").value = item?.args.join("\n") || "";
  $("#server-cwd").value = item?.cwd || "";
  $("#server-autostart").checked = Boolean(item?.autostart);
  $("#server-readonly").checked = Boolean(item?.read_only_auto);
  $("#server-name").focus();
}

async function saveServer(event) {
  event.preventDefault();
  const original = $("#server-original").value;
  const payload = {
    name: $("#server-name").value.trim(),
    command: $("#server-command").value.trim(),
    args: $("#server-args").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
    cwd: $("#server-cwd").value.trim() || null,
    autostart: $("#server-autostart").checked,
    read_only_auto: $("#server-readonly").checked,
  };
  try {
    state.settings = await api(original ? `mcp-servers/${encodeURIComponent(original)}` : "mcp-servers", { method: original ? "PUT" : "POST", body: JSON.stringify(payload) });
    $("#server-form").classList.add("hidden"); renderServerList(); await loadTools();
    toast("MCPサーバー設定を保存しました。");
  } catch (error) { toast(error.message, true); }
}

async function toggleServer(name, connected) {
  try {
    const status = await api(`mcp-servers/${encodeURIComponent(name)}/${connected ? "disconnect" : "connect"}`, { method: "POST" });
    const index = state.settings.mcp_statuses.findIndex((item) => item.name === name);
    if (index >= 0) state.settings.mcp_statuses[index] = status; else state.settings.mcp_statuses.push(status);
    renderServerList(); await loadTools();
    toast(`${name} を${connected ? "切断" : "接続"}しました。`);
  } catch (error) { toast(error.message, true); await loadSettings(); }
}

async function deleteServer(name) {
  if (!confirm(`MCPサーバー「${name}」を削除しますか？`)) return;
  try { state.settings = await api(`mcp-servers/${encodeURIComponent(name)}`, { method: "DELETE" }); renderServerList(); await loadTools(); }
  catch (error) { toast(error.message, true); }
}

async function loadTools() {
  const result = await api("tools");
  state.tools = result.tools;
  $("#tool-count").textContent = String(state.tools.length);
  const list = $("#tool-list"); list.textContent = "";
  if (!state.tools.length) { list.innerHTML = '<p class="muted">MCPサーバーを接続すると表示されます。</p>'; return; }
  for (const name of state.tools) { const pill = document.createElement("span"); pill.className = "tool-pill"; pill.textContent = name; pill.title = name; list.append(pill); }
}

async function loadStatus() {
  try {
    const status = await api("status");
    const ready = status.ollama.status === "ready";
    const summary = $("#system-summary");
    summary.textContent = "";
    const dot = document.createElement("span");
    dot.className = "status-dot";
    dot.style.background = ready ? "var(--green)" : "var(--amber)";
    summary.append(dot, document.createTextNode(ready ? "Ollama準備完了" : `Ollama: ${status.ollama.status}`));
  } catch (error) { $("#system-summary").textContent = "接続状態を取得できません"; }
}

async function loadSessions(selectId = null) {
  state.sessions = await api("sessions");
  renderSessionList();
  const target = selectId || state.current?.id;
  if (target && state.sessions.some((item) => item.id === target)) await openSession(target);
}

function renderSessionList() {
  const list = $("#session-list"); list.textContent = "";
  if (!state.sessions.length) { const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "まだチャットはありません。"; list.append(empty); }
  for (const session of state.sessions) {
    const button = document.createElement("button"); button.className = `session-item${state.current?.id === session.id ? " active" : ""}`;
    const title = document.createElement("strong"); title.textContent = session.title;
    const time = document.createElement("span"); time.textContent = formatDate(session.updated_at);
    button.append(title, time); button.addEventListener("click", () => openSession(session.id)); list.append(button);
  }
}

async function createSession(title = "新しいチャット") {
  try {
    const created = await api("sessions", { method: "POST", body: JSON.stringify({ title }) });
    await loadSessions(created.id); $("#chat-input").focus();
    return created;
  } catch (error) { toast(error.message, true); return null; }
}

async function openSession(id) {
  state.current = await api(`sessions/${id}`);
  $("#welcome").classList.add("hidden"); $("#chat-workspace").classList.remove("hidden");
  $("#session-title").textContent = state.current.title;
  renderSessionList(); renderMessages();
}

async function deleteCurrentSession() {
  if (!state.current || !confirm(`「${state.current.title}」の会話とツール履歴を削除しますか？`)) return;
  try { await api(`sessions/${state.current.id}`, { method: "DELETE" }); state.current = null; $("#chat-workspace").classList.add("hidden"); $("#welcome").classList.remove("hidden"); await loadSessions(); }
  catch (error) { toast(error.message, true); }
}

function renderMessages() {
  const list = $("#chat-messages"); list.textContent = "";
  for (const item of state.current.messages) list.append(renderMessage(item));
  const pendingId = state.current.state.pending_approval;
  if (pendingId) {
    const event = state.current.events.find((item) => item.id === pendingId && item.status === "pending");
    if (event) list.append(renderApproval(event));
  }
  scrollConversationToEnd();
}

function renderMessage(item) {
  const article = document.createElement("article"); article.className = `message ${item.role}`;
  if (item.role !== "user") { const avatar = document.createElement("div"); avatar.className = "avatar"; avatar.textContent = item.role === "tool" ? "T" : "U"; article.append(avatar); }
  const bubble = document.createElement("div"); bubble.className = "bubble";
  if (item.role !== "user") { const meta = document.createElement("div"); meta.className = "message-meta"; meta.textContent = item.role === "tool" ? item.metadata.tool_name || "MCP TOOL" : "LOCAL ASSISTANT"; bubble.append(meta); }
  const content = document.createElement("div"); content.className = "message-content"; content.textContent = item.content || (item.metadata.tool_calls ? "ツールの実行を準備しました。" : ""); bubble.append(content);
  appendPcaPlot(bubble, item);
  article.append(bubble);
  return article;
}

function appendMessage(item) {
  $("#chat-messages").append(renderMessage(item));
  scrollConversationToEnd();
}

function scrollConversationToEnd() {
  const workspace = $("#chat-workspace");
  workspace.scrollTop = workspace.scrollHeight;
}

function appendPcaPlot(bubble, item) {
  if (item.role !== "tool" || item.metadata?.is_error || !window.Plotly || !window.GeneralPcaPlot) return;
  const pca = window.GeneralPcaPlot.findPlot(item.content);
  if (!pca?.points?.length) return;

  bubble.classList.add("has-plot");
  const card = document.createElement("section"); card.className = "chat-plot-card";
  const heading = document.createElement("div"); heading.className = "chat-plot-heading";
  const title = document.createElement("strong"); title.textContent = pca.title;
  const note = document.createElement("span"); note.textContent = `${pca.points.length} samples · hover / zoom / legend filter`;
  heading.append(title, note);
  const plot = document.createElement("div"); plot.className = "chat-pca-plot"; plot.setAttribute("aria-label", `${pca.title} Plotly chart`);
  card.append(heading, plot); bubble.append(card);

  const draw = () => {
    Promise.resolve(Plotly.react(
      plot,
      window.GeneralPcaPlot.traces(pca.points),
      {
        margin: { l: 58, r: 22, t: 18, b: 52 },
        xaxis: { title: pca.xLabel, zeroline: false, gridcolor: "#ebe8e1" },
        yaxis: { title: pca.yLabel, zeroline: false, gridcolor: "#ebe8e1" },
        legend: { orientation: "h", y: -0.22 },
        hovermode: "closest",
        paper_bgcolor: "transparent",
        plot_bgcolor: "#ffffff",
        font: { family: '"Segoe UI Variable", "Yu Gothic UI", sans-serif', color: "#3d3b36", size: 11 },
      },
      { responsive: true, displaylogo: false, scrollZoom: true },
    )).then(() => {
      scrollConversationToEnd();
    });
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(draw); else draw();
}

function renderApproval(event) {
  const card = document.createElement("section"); card.className = "approval-card";
  const title = document.createElement("h3"); title.textContent = "ツール実行の確認";
  const reason = document.createElement("p"); reason.textContent = event.payload.decision?.reason || "この操作には確認が必要です。";
  const code = document.createElement("code"); code.textContent = `${event.payload.qualified_name}\n${JSON.stringify(event.payload.arguments, null, 2)}`;
  const actions = document.createElement("div"); actions.className = "form-actions";
  actions.append(actionButton("拒否", () => resolveApproval(event.id, false), "button quiet"), actionButton("実行を許可", () => resolveApproval(event.id, true), "button primary"));
  card.append(title, reason, code, actions); return card;
}

async function resolveApproval(eventId, approved) {
  if (!state.current) return;
  setBusy(true, approved ? "承認済みツールを実行中" : "拒否を伝達中");
  try {
    const result = await api(`sessions/${state.current.id}/approvals/${eventId}`, { method: "POST", body: JSON.stringify({ approved }) });
    await openSession(state.current.id);
    if (result.status === "approval_required") toast("次のツール呼び出しも確認が必要です。");
  } catch (error) { toast(error.message, true); }
  finally { setBusy(false); }
}

function setBusy(busy, label = "準備完了") {
  $("#send-chat").disabled = busy;
  $("#cancel-chat").classList.toggle("hidden", !busy);
  $("#composer-status").textContent = label;
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $("#chat-input"); const message = input.value.trim();
  if (!message) return;
  if (!state.current) { const created = await createSession(message.slice(0, 38)); if (!created) return; }
  input.value = "";
  const optimisticMessage = { role: "user", content: message, metadata: {} };
  state.current.messages.push(optimisticMessage);
  appendMessage(optimisticMessage);
  setBusy(true, "モデルが考えています");
  state.controller = new AbortController();
  try {
    const response = await fetch(`./api/sessions/${state.current.id}/chat/stream`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }), signal: state.controller.signal });
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n"); buffer = chunks.pop();
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const item = JSON.parse(line.slice(6));
        if (item.type === "status") $("#composer-status").textContent = item.status === "thinking" ? "モデルが考えています" : item.status;
        if (item.type === "error") throw new Error(item.error);
      }
    }
    await openSession(state.current.id); await loadSessions();
  } catch (error) {
    if (error.name === "AbortError") toast("応答を停止しました。"); else toast(error.message, true);
  } finally { state.controller = null; setBusy(false); input.focus(); }
}

function cancelChat() { state.controller?.abort(); }

function bindEvents() {
  $("#new-session").addEventListener("click", () => createSession());
  $("#delete-session").addEventListener("click", deleteCurrentSession);
  $("#chat-form").addEventListener("submit", sendMessage);
  $("#chat-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#chat-form").requestSubmit(); } });
  $("#cancel-chat").addEventListener("click", cancelChat);
  $("#refresh-status").addEventListener("click", async () => { await loadSettings(); await loadStatus(); toast("接続状態を更新しました。"); });
  $("#toggle-settings").addEventListener("click", () => $("#settings-panel").classList.toggle("open"));
  $("#close-settings").addEventListener("click", () => $("#settings-panel").classList.remove("open"));
  $("#add-endpoint").addEventListener("click", () => openEndpointForm());
  $("#endpoint-form").addEventListener("submit", saveEndpoint);
  $("#add-server").addEventListener("click", () => openServerForm());
  $("#server-form").addEventListener("submit", saveServer);
  document.querySelectorAll(".form-cancel").forEach((button) => button.addEventListener("click", () => button.closest("form").classList.add("hidden")));
  document.querySelectorAll(".starter").forEach((button) => button.addEventListener("click", async () => { $("#chat-input").value = button.dataset.prompt; $("#chat-form").requestSubmit(); }));
}

async function initialize() {
  bindEvents();
  try {
    await Promise.all([loadSettings(), loadSessions(), loadStatus()]);
    if (state.sessions.length) await openSession(state.sessions[0].id);
  } catch (error) { toast(error.message, true); }
}

initialize();
