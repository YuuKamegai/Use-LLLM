const $ = (selector) => document.querySelector(selector);
const csrfToken = document.querySelector('meta[name="use-lllm-csrf-token"]')?.content || "";
const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function requestHeaders(method = "GET", headers = {}) {
  const result = { "Content-Type": "application/json", ...headers };
  if (unsafeMethods.has(method.toUpperCase())) {
    if (!csrfToken || csrfToken === "__USE_LLLM_CSRF_TOKEN__") throw new Error("ローカルAPIの起動トークンがありません。WebUIを再読み込みしてください。");
    result["X-Use-LLLM-CSRF"] = csrfToken;
  }
  return result;
}

const state = {
  settings: null,
  sessions: [],
  current: null,
  tools: [],
  knowledge: [],
  attachments: [],
  controller: null,
  setup: null,
};

async function api(path, options = {}) {
  const { headers = {}, ...requestOptions } = options;
  const method = requestOptions.method || "GET";
  const response = await fetch(`./api/${path.replace(/^\//, "")}`, {
    ...requestOptions,
    headers: requestHeaders(method, headers),
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

function formatTokenCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count) || count < 0) return "—";
  if (count >= 1000) return `${(count / 1000).toFixed(count >= 10000 ? 1 : 2)}k`;
  return Math.round(count).toLocaleString("ja-JP");
}

function renderContextUsage() {
  const meter = $("#context-meter");
  const label = $("#context-meter-label");
  const fill = $("#context-meter-fill");
  const usage = state.current?.state?.context_usage;
  const remaining = Number(usage?.remaining_percent);
  meter.classList.remove("unknown", "warning", "critical");
  if (!usage || !Number.isFinite(remaining)) {
    meter.classList.add("unknown");
    label.textContent = "コンテキスト —";
    fill.style.width = "0%";
    meter.title = "最初の応答後に推定します";
    return;
  }
  const percent = Math.max(0, Math.min(100, Math.round(remaining)));
  const provisional = usage.context_window_confirmed === false;
  if (percent < 15) meter.classList.add("critical");
  else if (percent < 40) meter.classList.add("warning");
  label.textContent = `${provisional ? "暫定残り" : "残りコンテキスト"} ${percent}%`;
  fill.style.width = `${percent}%`;
  const compacted = usage.summary_created ? " 古い会話は直前の応答で要約されました。" : "";
  const source = provisional ? "モデル上限未確認の暫定値。" : "";
  const accuracy = usage.accuracy === "measured" ? "トークン使用量は実測。" : "トークン使用量は推定を含みます。";
  meter.title = `${source}自動要約まで ${formatTokenCount(usage.remaining_tokens)} / ${formatTokenCount(usage.input_budget)} tokens。使用 ${formatTokenCount(usage.used_tokens)}、モデル上限 ${formatTokenCount(usage.context_window)}。${accuracy}${compacted}`.trim();
}

async function loadSetup() {
  state.setup = await api("setup");
  renderSetup();
  return state.setup;
}

function renderSetup() {
  const setup = state.setup;
  const overlay = $("#setup-overlay");
  overlay.classList.toggle("hidden", Boolean(setup?.completed));
  if (!setup || setup.completed) return;
  $("#setup-ollama-download").href = setup.ollama_download_url;
  const ollama = setup.ollama || {};
  const models = Array.isArray(ollama.models) ? ollama.models : [];
  $("#setup-ollama-status").textContent = ollama.status === "unavailable"
    ? `未接続: ${ollama.error || "Ollamaをインストールして起動してください。"}`
    : `接続済み · ${models.length}モデル`;
  const datalist = $("#setup-models"); datalist.textContent = "";
  for (const model of models) { const option = document.createElement("option"); option.value = model; datalist.append(option); }
  const input = $("#setup-model");
  if (!input.dataset.touched) input.value = setup.selected_model || models[0] || "qwen3:14b";
  $("#setup-complete").disabled = ollama.status === "unavailable";
}

function setupProgress(item) {
  const completed = Number(item.completed || 0); const total = Number(item.total || 0);
  const percent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  $("#setup-progress-bar").style.width = `${percent}%`;
  $("#setup-progress-label").textContent = total > 0 ? `${item.status || "取得中"} · ${percent}%` : item.status || "取得中";
}

async function pullSetupModel() {
  const model = $("#setup-model").value.trim();
  if (!model) return;
  $("#setup-error").textContent = ""; $("#setup-pull-model").disabled = true;
  try {
    const response = await fetch("./api/setup/models/pull", { method: "POST", headers: requestHeaders("POST"), body: JSON.stringify({ model }) });
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true }); const lines = buffer.split("\n"); buffer = lines.pop();
      for (const line of lines) { if (!line.trim()) continue; const item = JSON.parse(line); if (item.error) throw new Error(item.error); setupProgress(item); }
    }
    await loadSetup();
  } catch (error) { $("#setup-error").textContent = error.message; }
  finally { $("#setup-pull-model").disabled = false; }
}

async function completeSetup() {
  const model = $("#setup-model").value.trim();
  $("#setup-error").textContent = ""; $("#setup-complete").disabled = true;
  try {
    await api("setup/complete", { method: "POST", body: JSON.stringify({ model }) });
    await Promise.all([loadSetup(), loadSettings(), loadStatus()]);
    toast("初回セットアップが完了しました。");
  } catch (error) { $("#setup-error").textContent = error.message; }
  finally { if (!state.setup?.completed) $("#setup-complete").disabled = false; }
}

async function configureAzureFromSetup() {
  $("#setup-error").textContent = "";
  try {
    await api("setup/connections", { method: "POST" });
    await Promise.all([loadSetup(), loadSettings()]);
    $("#settings-panel").classList.add("open");
    openEndpointForm(null, "azure_openai");
    toast("Azure OpenAIの接続情報を入力してください。");
  } catch (error) { $("#setup-error").textContent = error.message; }
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
  const azure = endpoint.provider === "azure_openai";
  const remote = endpoint.trust !== "loopback";
  banner.classList.toggle("remote", remote);
  banner.querySelector("small").textContent = azure ? "Azure OpenAI" : remote ? "LAN送信先" : "ローカル送信先";
  banner.querySelector("strong").textContent = `${endpoint.name} · ${endpoint.base_url} · ${endpoint.default_model || "モデル未設定"}`;
  banner.title = remote ? `プロンプトとMCPツール結果は ${endpoint.base_url} へ送信されます` : "このPC上のOllamaへ送信します";
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
    const provider = item.provider === "azure_openai" ? "Azure OpenAI" : "Ollama";
    const context = item.context_window ? ` · context ${formatTokenCount(item.context_window)}` : "";
    detail.textContent = `${provider} · ${item.base_url} · ${item.default_model || "モデル未設定"}${context}`;
    info.append(title, detail);
    const chip = document.createElement("span");
    chip.className = `state-chip${item.name === state.settings.selected_endpoint ? "" : " off"}`;
    chip.textContent = item.name === state.settings.selected_endpoint ? "選択中" : item.trust;
    top.append(info, chip);
    const actions = document.createElement("div");
    actions.className = "connection-actions";
    if (item.name !== state.settings.selected_endpoint) actions.append(actionButton("選択", () => selectEndpoint(item.name)));
    actions.append(actionButton("接続テスト", () => testEndpoint(item.name)));
    actions.append(actionButton("編集", () => openEndpointForm(item)));
    actions.append(actionButton("削除", () => deleteEndpoint(item.name), "mini-button danger-text"));
    card.append(top, actions);
    list.append(card);
  }
}

function updateEndpointProviderFields() {
  const azure = $("#endpoint-provider").value === "azure_openai";
  const creating = !$("#endpoint-original").value;
  $("#endpoint-api-key-row").classList.toggle("hidden", !azure);
  $("#endpoint-context-window-row").classList.toggle("hidden", !azure);
  $("#endpoint-url").placeholder = azure ? "https://YOUR-RESOURCE.openai.azure.com" : "http://127.0.0.1:11434";
  $("#endpoint-model").placeholder = azure ? "Azureのdeployment名" : "qwen3:14b";
  $("#endpoint-provider-help").textContent = azure
    ? "Azure portalのEndpoint、deployment名、API keyを入力してください。ローカルMCPはこのPCで実行されます。"
    : "Ollamaのローカルまたは明示許可したLAN endpointを指定します。";
  if (azure) $("#endpoint-trust").value = "cloud_allowed";
  else if ($("#endpoint-trust").value === "cloud_allowed") $("#endpoint-trust").value = "loopback";
  if (creating && azure && $("#endpoint-url").value === "http://127.0.0.1:11434") $("#endpoint-url").value = "";
  if (creating && azure && $("#endpoint-model").value === "qwen3:14b") $("#endpoint-model").value = "";
  if (creating && !azure && !$("#endpoint-url").value) $("#endpoint-url").value = "http://127.0.0.1:11434";
  if (creating && !azure && !$("#endpoint-model").value) $("#endpoint-model").value = "qwen3:14b";
  $("#endpoint-trust").disabled = azure;
}

function openEndpointForm(item = null, requestedProvider = null) {
  const form = $("#endpoint-form");
  form.classList.remove("hidden");
  const provider = item?.provider || requestedProvider || "ollama";
  $("#endpoint-original").value = item?.name || "";
  $("#endpoint-name").value = item?.name || "";
  $("#endpoint-provider").value = provider;
  $("#endpoint-url").value = item?.base_url || (provider === "azure_openai" ? "" : "http://127.0.0.1:11434");
  $("#endpoint-model").value = item?.default_model || (provider === "azure_openai" ? "" : "qwen3:14b");
  $("#endpoint-api-key").value = "";
  $("#endpoint-context-window").value = item?.context_window || "";
  $("#endpoint-api-key").placeholder = item?.api_key_configured
    ? "保存済み（同じEndpointなら空欄で保持、変更時は再入力）"
    : "Azure OpenAI API key";
  $("#endpoint-trust").value = item?.trust || (provider === "azure_openai" ? "cloud_allowed" : "loopback");
  updateEndpointProviderFields();
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
    provider: $("#endpoint-provider").value,
    api_key: $("#endpoint-api-key").value.trim() || null,
    context_window: $("#endpoint-context-window").value
      ? Number.parseInt($("#endpoint-context-window").value, 10)
      : null,
  };
  try {
    state.settings = await api(original ? `endpoints/${encodeURIComponent(original)}` : "endpoints", { method: original ? "PUT" : "POST", body: JSON.stringify(payload) });
    $("#endpoint-form").classList.add("hidden");
    renderEndpointBanner(); renderEndpointList(); renderServerList(); await loadTools();
    toast("AI接続を保存しました。");
  } catch (error) { toast(error.message, true); }
}

async function selectEndpoint(name) {
  try {
    state.settings = await api(`endpoints/${encodeURIComponent(name)}/select`, { method: "POST" });
    renderEndpointBanner(); renderEndpointList();
    toast(`${name} を送信先に選択しました。`);
  } catch (error) { toast(error.message, true); }
}

async function testEndpoint(name) {
  try {
    const result = await api(`endpoints/${encodeURIComponent(name)}/test`, { method: "POST" });
    toast(`${name}: ${result.status || "接続成功"}`);
  } catch (error) { toast(error.message, true); }
}

async function deleteEndpoint(name) {
  if (!confirm(`AI接続「${name}」を削除しますか？`)) return;
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
    const detail = document.createElement("small"); detail.textContent = item.transport === "stdio" ? `${item.command} ${item.args.join(" ")}`.trim() : `${item.transport} · ${item.url}`;
    info.append(title, detail);
    const chip = document.createElement("span");
    chip.className = `state-chip${status.status === "connected" ? "" : " off"}`;
    chip.textContent = status.status === "connected" ? `${status.tool_count} tools` : status.status;
    top.append(info, chip);
    const actions = document.createElement("div"); actions.className = "connection-actions";
    actions.append(actionButton(status.status === "connected" ? "切断" : "接続", () => toggleServer(item.name, status.status === "connected")));
    actions.append(actionButton("診断", () => diagnoseServer(item.name)));
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
  $("#server-transport").value = item?.transport || "stdio";
  $("#server-command").value = item?.command || "";
  $("#server-args").value = item?.args.join("\n") || "";
  $("#server-cwd").value = item?.cwd || "";
  $("#server-url").value = item?.url || "";
  $("#server-auth").value = item?.auth_mode || "none";
  $("#server-env").value = "";
  $("#server-env").placeholder = item?.env_keys?.length ? `保存済みキー: ${item.env_keys.join(", ")}（空欄なら保持）` : "KEY=VALUE";
  $("#server-headers").value = "";
  $("#server-headers").placeholder = item?.header_keys?.length ? `保存済みキー: ${item.header_keys.join(", ")}（空欄なら保持）` : "Authorization=Bearer ...";
  $("#server-autostart").checked = Boolean(item?.autostart);
  $("#server-readonly").checked = Boolean(item?.read_only_auto);
  $("#server-name").focus();
}

function parsePairs(value) {
  return Object.fromEntries(value.split(/\r?\n/).filter((line) => line.trim()).map((line) => {
    const index = line.indexOf("=");
    if (index < 1) throw new Error(`KEY=VALUE形式ではありません: ${line}`);
    return [line.slice(0, index).trim(), line.slice(index + 1)];
  }));
}

async function saveServer(event) {
  event.preventDefault();
  const original = $("#server-original").value;
  try {
    const envText = $("#server-env").value;
    const headerText = $("#server-headers").value;
    const payload = {
      name: $("#server-name").value.trim(), transport: $("#server-transport").value,
      command: $("#server-command").value.trim(),
      args: $("#server-args").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
      cwd: $("#server-cwd").value.trim() || null, url: $("#server-url").value.trim() || null,
      auth_mode: $("#server-auth").value,
      env: original && !envText.trim() ? null : parsePairs(envText),
      headers: original && !headerText.trim() ? null : parsePairs(headerText),
      autostart: $("#server-autostart").checked,
      read_only_auto: $("#server-readonly").checked,
    };
    state.settings = await api(original ? `mcp-servers/${encodeURIComponent(original)}` : "mcp-servers", { method: original ? "PUT" : "POST", body: JSON.stringify(payload) });
    $("#server-form").classList.add("hidden"); renderServerList(); await loadTools();
    toast("MCPサーバー設定を保存しました。");
  } catch (error) { toast(error.message, true); }
}

async function diagnoseServer(name) {
  try {
    const result = await api(`mcp-servers/${encodeURIComponent(name)}/diagnostics`);
    toast(`${name}: ${result.status.status} / ${result.transport}`);
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
  const disabled = new Set(state.current?.state?.disabled_tools || []);
  for (const name of state.tools) {
    const label = document.createElement("label"); label.className = "tool-pill tool-toggle";
    const check = document.createElement("input"); check.type = "checkbox"; check.checked = !disabled.has(name);
    check.addEventListener("change", () => updateToolPreference(name, check.checked));
    label.append(check, document.createTextNode(name)); label.title = name; list.append(label);
  }
}

async function updateToolPreference(name, enabled) {
  if (!state.current) { toast("先にチャットを作成してください。", true); await loadTools(); return; }
  const disabled = new Set(state.current.state.disabled_tools || []);
  if (enabled) disabled.delete(name); else disabled.add(name);
  try {
    state.current = await api(`sessions/${state.current.id}/tools`, { method: "PUT", body: JSON.stringify({ disabled_tools: [...disabled] }) });
  } catch (error) { toast(error.message, true); }
  await loadTools();
}

async function loadStatus() {
  try {
    const status = await api("status");
    const model = status.model || status.ollama || {};
    const ready = ["ready", "configured"].includes(model.status);
    const provider = model.provider === "azure_openai" ? "Azure OpenAI" : "Ollama";
    const summary = $("#system-summary");
    summary.textContent = "";
    const dot = document.createElement("span");
    dot.className = "status-dot";
    dot.style.background = ready ? "var(--green)" : "var(--amber)";
    summary.append(dot, document.createTextNode(ready ? `${provider}準備完了` : `${provider}: ${model.status}`));
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
  renderSessionList(); renderMessages(); renderContextUsage(); await loadTools();
}

function applySessionTitle(title) {
  if (!state.current || !title) return;
  state.current.title = title;
  const summary = state.sessions.find((item) => item.id === state.current.id);
  if (summary) summary.title = title;
  $("#session-title").textContent = title;
  renderSessionList();
}

async function deleteCurrentSession() {
  if (!state.current || !confirm(`「${state.current.title}」の会話とツール履歴を削除しますか？`)) return;
  try { await api(`sessions/${state.current.id}`, { method: "DELETE" }); state.current = null; renderContextUsage(); $("#chat-workspace").classList.add("hidden"); $("#welcome").classList.remove("hidden"); await loadSessions(); }
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
  const content = document.createElement("div"); content.className = "message-content";
  const text = item.content || (item.metadata.tool_calls ? "ツールの実行を準備しました。" : "");
  if (item.role === "assistant" && window.GeneralMarkdown) content.innerHTML = window.GeneralMarkdown.renderMarkdown(text);
  else content.textContent = text;
  content.querySelectorAll?.(".copy-code").forEach((button) => button.addEventListener("click", () => navigator.clipboard.writeText(button.nextElementSibling.textContent)));
  bubble.append(content);
  if (item.role === "tool" && window.GeneralArtifacts) window.GeneralArtifacts.append(bubble, item);
  appendPcaPlot(bubble, item);
  appendEicPlot(bubble, item);
  appendVolcanoPlot(bubble, item);
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

function appendEicPlot(bubble, item) {
  if (item.role !== "tool" || item.metadata?.is_error || !window.Plotly || !window.GeneralEicPlot) return;
  const eic = window.GeneralEicPlot.findPlot(item.content);
  if (!eic?.series?.length) return;

  bubble.classList.add("has-plot");
  const card = document.createElement("section"); card.className = "chat-plot-card";
  const heading = document.createElement("div"); heading.className = "chat-plot-heading";
  const title = document.createElement("strong"); title.textContent = eic.title;
  const note = document.createElement("span"); note.textContent = `${eic.series.length} traces · hover / zoom / legend filter`;
  heading.append(title, note);
  const plot = document.createElement("div"); plot.className = "chat-pca-plot";
  plot.setAttribute("aria-label", `${eic.title} Plotly chart`);
  card.append(heading, plot); bubble.append(card);

  // 多系列（複数物質オーバーレイ）では横並び凡例が潰れるので右外側の縦並びにする。
  const manySeries = eic.series.length > 8;
  const draw = () => {
    Promise.resolve(Plotly.react(
      plot,
      window.GeneralEicPlot.traces(eic),
      {
        margin: { l: 68, r: manySeries ? 200 : 22, t: 18, b: 52 },
        xaxis: { title: eic.xLabel, zeroline: false, gridcolor: "#ebe8e1" },
        yaxis: { title: eic.yLabel, rangemode: "tozero", zeroline: false, gridcolor: "#ebe8e1" },
        legend: manySeries
          ? { orientation: "v", x: 1.02, xanchor: "left", y: 1, font: { size: 9 } }
          : { orientation: "h", y: -0.22 },
        annotations: window.GeneralEicPlot.annotations(eic),
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

function appendVolcanoPlot(bubble, item) {
  if (item.role !== "tool" || item.metadata?.is_error || !window.Plotly || !window.GeneralVolcanoPlot) return;
  const volcano = window.GeneralVolcanoPlot.findPlot(item.content);
  if (!volcano?.points?.length) return;

  bubble.classList.add("has-plot");
  const card = document.createElement("section"); card.className = "chat-plot-card";
  const heading = document.createElement("div"); heading.className = "chat-plot-heading";
  const title = document.createElement("strong"); title.textContent = volcano.title;
  const note = document.createElement("span");
  note.textContent = window.GeneralVolcanoPlot.note(volcano);
  heading.append(title, note);
  const plot = document.createElement("div"); plot.className = "chat-pca-plot";
  plot.setAttribute("aria-label", `${volcano.title} Plotly chart`);
  card.append(heading, plot); bubble.append(card);

  const draw = () => {
    Promise.resolve(Plotly.react(
      plot,
      window.GeneralVolcanoPlot.traces(volcano),
      {
        margin: { l: 58, r: 22, t: 18, b: 52 },
        xaxis: { title: volcano.xLabel, zeroline: false, gridcolor: "#ebe8e1" },
        yaxis: { title: volcano.yLabel, rangemode: "tozero", zeroline: false, gridcolor: "#ebe8e1" },
        shapes: window.GeneralVolcanoPlot.shapes(volcano),
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
    const response = await fetch(`./api/sessions/${state.current.id}/chat/stream`, { method: "POST", headers: requestHeaders("POST"), body: JSON.stringify({ message, attachment_ids: state.attachments }), signal: state.controller.signal });
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    let streaming = null; let streamedContent = "";
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n"); buffer = chunks.pop();
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const item = JSON.parse(line.slice(6));
        if (item.session_title) applySessionTitle(item.session_title);
        if (item.type === "status") $("#composer-status").textContent = item.status === "thinking" ? "モデルが考えています" : item.status;
        if (item.type === "delta") {
          streamedContent += item.content || "";
          if (!streaming) {
            streaming = renderMessage({ role: "assistant", content: "", metadata: {} });
            streaming.classList.add("streaming"); $("#chat-messages").append(streaming);
          }
          const node = streaming.querySelector(".message-content");
          if (window.GeneralMarkdown) node.innerHTML = window.GeneralMarkdown.renderMarkdown(streamedContent);
          else node.textContent = streamedContent;
          scrollConversationToEnd();
        }
        if (item.type === "tool_started") {
          $("#composer-status").textContent = `${item.tool} を実行中`;
          appendMessage({ role: "tool", content: `実行中: ${item.tool}\n${JSON.stringify(item.arguments || {}, null, 2)}`, metadata: { tool_name: item.tool } });
        }
        if (item.type === "tool_result") $("#composer-status").textContent = `${item.tool} が完了`;
        if (item.type === "approval_required") $("#composer-status").textContent = "ツール実行の確認待ち";
        if (item.context_usage && state.current) {
          state.current.state.context_usage = item.context_usage;
          renderContextUsage();
        }
        if (item.type === "error") throw new Error(item.error);
      }
    }
    state.attachments = []; renderAttachmentChips();
    await openSession(state.current.id); await loadSessions();
  } catch (error) {
    if (error.name === "AbortError") toast("応答を停止しました。"); else toast(error.message, true);
  } finally { state.controller = null; setBusy(false); input.focus(); }
}

function cancelChat() { state.controller?.abort(); }

async function loadKnowledge(query = "") {
  const result = await api(query ? `knowledge/search?q=${encodeURIComponent(query)}` : "knowledge");
  state.knowledge = result.sources;
  const list = $("#knowledge-list"); list.textContent = "";
  if (!state.knowledge.length) { list.innerHTML = '<p class="muted">保存済み資料はありません。</p>'; return; }
  for (const source of state.knowledge) {
    const card = document.createElement("article"); card.className = "connection-card knowledge-card";
    const check = document.createElement("input"); check.type = "checkbox"; check.checked = state.attachments.includes(source.id);
    check.addEventListener("change", () => { if (check.checked) state.attachments.push(source.id); else state.attachments = state.attachments.filter((id) => id !== source.id); renderAttachmentChips(); });
    const info = document.createElement("span"); info.textContent = `${source.name} · ${Math.ceil(source.size / 1024)}KB`;
    const remove = actionButton("削除", () => deleteKnowledge(source.id), "mini-button danger-text");
    card.append(check, info, remove); list.append(card);
  }
}

function renderAttachmentChips() {
  const area = $("#attachment-chips"); area.textContent = "";
  for (const id of state.attachments) {
    const source = state.knowledge.find((item) => item.id === id);
    const chip = document.createElement("button"); chip.type = "button"; chip.className = "attachment-chip";
    chip.textContent = `${source?.name || id} ×`; chip.addEventListener("click", () => { state.attachments = state.attachments.filter((value) => value !== id); renderAttachmentChips(); loadKnowledge($("#knowledge-search").value); });
    area.append(chip);
  }
}

async function uploadKnowledge(event) {
  for (const file of event.target.files) {
    if (file.size > 5 * 1024 * 1024) { toast(`${file.name} は5MBを超えています。`, true); continue; }
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = ""; for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    try { await api("knowledge", { method: "POST", body: JSON.stringify({ name: file.name, mime_type: file.type || "application/octet-stream", content_base64: btoa(binary) }) }); }
    catch (error) { toast(error.message, true); }
  }
  event.target.value = ""; await loadKnowledge();
}

async function deleteKnowledge(id) {
  if (!confirm("この資料をKnowledgeから削除しますか？")) return;
  try { await api(`knowledge/${id}`, { method: "DELETE" }); state.attachments = state.attachments.filter((value) => value !== id); await loadKnowledge(); renderAttachmentChips(); }
  catch (error) { toast(error.message, true); }
}

async function loadCapabilities() {
  try {
    const [resourceData, promptData] = await Promise.all([api("resources"), api("prompts")]);
    renderCapabilities($("#resource-list"), resourceData.resources, "resource");
    renderCapabilities($("#prompt-list"), promptData.prompts, "prompt");
  } catch (error) { toast(error.message, true); }
}

function renderCapabilities(list, values, kind) {
  list.textContent = "";
  for (const item of values) {
    const card = document.createElement("button"); card.type = "button"; card.className = "connection-card capability-card";
    card.textContent = `${item.server} · ${item.name || item.uri || item.uriTemplate}`;
    card.addEventListener("click", async () => {
      try {
        const result = kind === "resource" && item.uri
          ? await api("resources/read", { method: "POST", body: JSON.stringify({ server: item.server, uri: item.uri }) })
          : kind === "prompt" ? await api("prompts/get", { method: "POST", body: JSON.stringify({ server: item.server, name: item.name, arguments: {} }) }) : item;
        $("#chat-input").value = JSON.stringify(result, null, 2);
        toast("内容を入力欄へ展開しました。");
      } catch (error) { toast(error.message, true); }
    });
    list.append(card);
  }
}

async function importClaude() {
  try {
    const config = JSON.parse($("#claude-config").value);
    const result = await api("mcp-servers/import-claude", { method: "POST", body: JSON.stringify({ config }) });
    state.settings = result.settings; renderServerList(); toast(`${result.imported.length}件を取り込みました。`);
  } catch (error) { toast(error.message, true); }
}

function applyServerPreset() {
  const preset = $("#server-preset").value;
  if (preset === "filesystem") { $("#server-name").value = "filesystem"; $("#server-command").value = "npx"; $("#server-args").value = "-y\n@modelcontextprotocol/server-filesystem\nC:\\Users"; }
  if (preset === "github") { $("#server-name").value = "github"; $("#server-command").value = "npx"; $("#server-args").value = "-y\n@modelcontextprotocol/server-github"; $("#server-env").value = "GITHUB_PERSONAL_ACCESS_TOKEN="; }
  if (preset === "http") { $("#server-transport").value = "streamable_http"; $("#server-url").focus(); }
}

function bindEvents() {
  $("#setup-model").addEventListener("input", (event) => { event.target.dataset.touched = "true"; });
  $("#setup-refresh").addEventListener("click", loadSetup);
  $("#setup-pull-model").addEventListener("click", pullSetupModel);
  $("#setup-complete").addEventListener("click", completeSetup);
  $("#setup-use-azure").addEventListener("click", configureAzureFromSetup);
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
  $("#endpoint-provider").addEventListener("change", updateEndpointProviderFields);
  $("#add-server").addEventListener("click", () => openServerForm());
  $("#server-form").addEventListener("submit", saveServer);
  $("#server-preset").addEventListener("change", applyServerPreset);
  $("#knowledge-file").addEventListener("change", uploadKnowledge);
  $("#knowledge-search").addEventListener("input", (event) => loadKnowledge(event.target.value));
  $("#refresh-capabilities").addEventListener("click", loadCapabilities);
  $("#import-claude").addEventListener("click", importClaude);
  document.querySelectorAll(".form-cancel").forEach((button) => button.addEventListener("click", () => button.closest("form").classList.add("hidden")));
  document.querySelectorAll(".starter").forEach((button) => button.addEventListener("click", async () => { $("#chat-input").value = button.dataset.prompt; $("#chat-form").requestSubmit(); }));
}

async function initialize() {
  bindEvents();
  try {
    await Promise.all([loadSetup(), loadSettings(), loadSessions(), loadStatus(), loadKnowledge(), loadCapabilities()]);
    if (state.sessions.length) await openSession(state.sessions[0].id);
  } catch (error) { toast(error.message, true); }
}

initialize();
