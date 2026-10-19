(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.GeneralI18n = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const english = {
    "Web検索": "Web search",
    "Web検索と公開ページ取得を有効化": "Enable Web search and public page reading",
    "APIキー不要 / DuckDuckGo": "No API key required / DuckDuckGo",
    "有効時はAIが必要に応じて検索し、検索・ページ取得を個別確認なしで実行します。検索語は検索先へ、取得URLはページの提供元へ送信されます。": "When enabled, the AI searches and reads public pages as needed without individual approval. Search queries go to the search service; page URLs go to their publishers.",
    "MCPサーバーの外部通信許可とは独立した設定です。": "This setting is independent of MCP server network permissions.",
    "オン": "On",
    "オフ": "Off",
    "Web検索中": "Searching the Web",
    "ページを確認中": "Reading a page",
    "ツール実行でエラーが発生しました": "A tool returned an error",
    " 古い会話は直前の応答で要約されました。": " Older messages were summarized before the last response.",
    "5MB以下のテキスト資料をローカル保存し、選択した資料だけを会話へ添付します。": "Store text documents up to 5 MB locally. Only selected documents are attached to the conversation.",
    '<p class="muted">MCPサーバーは未登録です。</p>': '<p class="muted">No MCP servers registered.</p>',
    '<p class="muted">MCPサーバーを接続すると表示されます。</p>': '<p class="muted">Connect an MCP server to see tools.</p>',
    '<p class="muted">保存済み資料はありません。</p>': '<p class="muted">No saved documents.</p>',
    "AI接続「{0}」を削除しますか？": "Delete AI connection “{0}”?",
    "AI接続を保存しました。": "AI connection saved.",
    "Azure OpenAIの接続情報を入力してください。": "Enter your Azure OpenAI connection details.",
    "Azure OpenAIを設定": "Configure Azure OpenAI",
    "Azure portalのEndpoint、deployment名、API keyを入力してください。ローカルMCPはこのPCで実行されます。": "Enter the endpoint, deployment name, and API key from Azure portal. Local MCP tools run on this PC.",
    "Azureのdeployment名": "Azure deployment name",
    "Azureクラウド送信を許可": "Allow sending to Azure cloud",
    "Claude Desktopから取込": "Import from Claude Desktop",
    "HTTP headers（KEY=VALUE、1行1件）": "HTTP headers (KEY=VALUE, one per line)",
    "KEY=VALUE形式ではありません: {0}": "Expected KEY=VALUE: {0}",
    "LAN送信を明示許可": "Explicitly allow LAN requests",
    "LAN送信先": "LAN destination",
    "MCPサーバー「{0}」を削除しますか？": "Delete MCP server “{0}”?",
    "MCPサーバーを接続すると表示されます。": "Connect an MCP server to see tools.",
    "MCPサーバー設定を保存しました。": "MCP server settings saved.",
    "MCP（任意）": "MCP (optional)",
    "Ollamaのローカルまたは明示許可したLAN endpointを指定します。": "Use a local Ollama endpoint or an explicitly allowed LAN endpoint.",
    "OllamaまたはAzure OpenAIへ相談し、このPCへ接続したMCPツールを必要なときだけ使います。書き込みや未知の操作は、あなたが確認するまで止まります。": "Chat with Ollama or Azure OpenAI and use MCP tools connected to this PC when needed. Writes and unknown operations wait for your approval.",
    "Ollamaをインストールして起動してください。": "Install and start Ollama.",
    "Ollamaを準備するか、Azure OpenAIをCONNECTIONSへ登録します。MCPサーバーはどちらのAIでも利用できます。": "Set up Ollama or add Azure OpenAI in Connections. MCP servers work with either AI provider.",
    "Use-LLLMを準備する": "Set up Use-LLLM",
    "html書き出し": "Export HTML",
    "loopbackのみ": "Loopback only",
    "ns を {0}→{1} に間引き": "ns downsampled from {0} to {1}",
    "{0} が完了": "{0} completed",
    "{0} は5MBを超えています。": "{0} exceeds 5 MB.",
    "{0} を{1}しました。": "{0}: {1}.",
    "{0} を実行中": "Running {0}",
    "{0} を送信先に選択しました。": "Selected {0} as the destination.",
    "{0}{1}自動要約まで {2} / {3} tokens（会話枠）。{4}使用 {5}、入力予算 {6}、モデル上限 {7}。{8}{9}": "{0}{1}{2} / {3} conversation tokens until summarization. {4}Used {5}; input budget {6}; model limit {7}. {8}{9}",
    "{0}件を取り込みました。": "Imported {0} items.",
    "{0}準備完了": "{0} ready",
    "「{0}」の会話とツール履歴を削除しますか？": "Delete the conversation and tool history for “{0}”?",
    "このPCで利用できるMCPツールを整理して、できることを教えてください。": "List the MCP tools available on this PC and explain what they can do.",
    "このPC上のOllamaへ送信します": "Sent to Ollama on this PC",
    "この操作には確認が必要です。": "This operation requires approval.",
    "この資料をKnowledgeから削除しますか？": "Delete this document from Knowledge?",
    "なし / header": "None / header",
    "まだチャットはありません。": "No conversations yet.",
    "コマンド": "Command",
    "コンテキスト —": "Context —",
    "コンテキスト上限（任意）": "Context limit (optional)",
    "セッション名": "Session name",
    "チャット": "Chats",
    "チャット履歴": "Chat history",
    "ツールの実行を準備しました。": "Prepared a tool call.",
    "ツール出力枠の高さを調整": "Resize tool output",
    "ツール定義が予算超過": "Tool definitions exceed budget",
    "ツール実行の確認": "Approve tool execution",
    "ツール実行の確認待ち": "Waiting for tool approval",
    "プロンプトは実測値、応答分は本文から推定（思考トークンは次のターンに残らないため除外）。": "Prompt tokens are measured; the reply is estimated from its text (reasoning tokens are excluded because they are not carried into the next turn). ",
    "トークン使用量は推定値。": "Token usage is estimated. ",
    "ファイル追加": "Add files",
    "プリセット": "Preset",
    "プロットの高さを調整": "Resize plot",
    "プロンプトとMCPツール結果は {0} へ送信されます": "Prompts and MCP tool results are sent to {0}",
    "メッセージ": "Message",
    "メッセージを入力。Shift + Enterで改行": "Enter a message. Shift + Enter for a new line",
    "モデル / Deployment": "Model / Deployment",
    "モデルが考えています": "Model is thinking",
    "モデルの回答やツール結果は重要な判断の前に確認してください。": "Review model responses and tool results before making important decisions.",
    "モデルを取得": "Download model",
    "モデル上限未確認の暫定値。": "Provisional: model context limit is unconfirmed. ",
    "モデル名": "Model name",
    "モデル未設定": "No model selected",
    "ローカルAPIの起動トークンがありません。WebUIを再読み込みしてください。": "Local API startup token is missing. Reload the WebUI.",
    "ローカルの道具を。": "Local tools.",
    "ローカル送信先": "Local destination",
    "会話とツール履歴はこのPCだけに保存されます。": "Conversations and tool history are saved only on this PC.",
    "会話モデル": "Chat model",
    "作業フォルダ": "Working directory",
    "作業手順を相談": "Plan a task",
    "保存": "Save",
    "保存済みキー: {0}（空欄なら保持）": "Saved keys: {0} (leave blank to keep)",
    "保存済み（同じEndpointなら空欄で保持、変更時は再入力）": "Saved (leave blank for the same endpoint; re-enter when changing it)",
    "停止": "Stop",
    "先にチャットを作成してください。": "Create a conversation first.",
    "公式インストーラーを開く": "Open official installer",
    "内容を入力欄へ展開しました。": "Content inserted into the message field.",
    "再確認": "Check again",
    "切断": "Disconnect",
    "初回セットアップが完了しました。": "Initial setup completed.",
    "初期状態ではMCPサーバーを登録しません。セットアップ後の「接続と設定」から追加できます。": "No MCP servers are registered by default. Add them in Connections & settings after setup.",
    "利用できるツールを確認": "Explore available tools",
    "削除": "Delete",
    "取得中": "Downloading",
    "取消": "Cancel",
    "取込": "Import",
    "名前": "Name",
    "固定費だけで入力予算を超えているため自動要約を止めています。num_ctx を上げるか、不要なツールを無効化してください。": "Automatic summarization is paused because fixed costs exceed the input budget. Increase num_ctx or disable unused tools. ",
    "固定費（ツール定義・システム指示）{0}。": "Fixed cost (tools and system instructions) {0}. ",
    "外部接続ではプロンプトとMCPツール結果が指定先へ送信されます。API keyはブラウザへ再表示しません。": "External connections send prompts and MCP tool results to the specified destination. Saved API keys are never displayed in the browser.",
    "実行を許可": "Allow execution",
    "実行中: {0}\n{1}": "Running: {0}\n{1}",
    "履歴を削除": "Delete history",
    "引数（1行1引数）": "Arguments (one per line)",
    "応答を停止しました。": "Response stopped.",
    "承認済みツールを実行中": "Running approved tool",
    "拒否": "Deny",
    "拒否を伝達中": "Sending denial",
    "接続": "Connect",
    "接続と設定": "Connections & settings",
    "接続テスト": "Test connection",
    "接続成功": "Connected successfully",
    "接続済み · {0}モデル": "Connected · {0} models",
    "接続状態を取得できません": "Unable to retrieve connection status",
    "接続状態を更新しました。": "Connection status refreshed.",
    "新しいチャット": "New chat",
    "既存モデルを選ぶか、Ollamaから取得します。既定は品質優先のqwen3:14bです。": "Select an existing model or download one from Ollama. The default is qwen3:14b for quality.",
    "既知モデルは自動判定": "Detected automatically for known models",
    "日本語": "日本語",
    "要約まで残り（暫定）": "Until summary (provisional)",
    "更新": "Refresh",
    "最初の応答後に推定します": "Estimated after the first response",
    "有意 {0} 件": "{0} significant",
    "未接続: {0}": "Disconnected: {0}",
    "未知・書き込み・外部通信ツールは、この設定に関係なく実行前に確認します。外部通信ツールは、許可しない限り実行できません。": "Unknown tools, writes, and external communication always require approval regardless of this setting. External-network tools cannot run unless allowed.",
    "未設定": "Not configured",
    "未開始": "Not started",
    "検定不能 {0} 件を除外": "Excluded {0} untestable points",
    "構造化データ": "Structured data",
    "次のツール呼び出しも確認が必要です。": "The next tool call also requires approval.",
    "次の作業を安全な手順に分解してください。": "Break the following task into safe steps.",
    "要約まで残り": "Until summary",
    "準備を完了": "Finish setup",
    "準備完了": "Ready",
    "状態を更新": "Refresh status",
    "状態を確認中": "Checking status",
    "環境変数（KEY=VALUE、1行1件）": "Environment variables (KEY=VALUE, one per line)",
    "画像を原寸で開く": "Open full-size image",
    "画面切替": "Navigation",
    "確定read-onlyツールを自動実行": "Automatically run confirmed read-only tools",
    "外部通信ツールを許可（全セッション）": "Allow external-network tools (all sessions)",
    "確認中...": "Checking...",
    "結果を慎重に解釈": "Interpret results carefully",
    "編集": "Edit",
    "編集時は空欄のままで保存済みキーを保持": "Leave blank to keep the saved key when editing",
    "衝突を避けるため server::tool 形式で表示します。": "Tools use server::tool names to avoid collisions.",
    "表示言語": "Display language",
    "表示言語 / Language": "Language / 表示言語",
    "観測結果と推測を分けて説明してください。": "Explain observations separately from interpretations.",
    "設定を閉じる": "Close settings",
    "診断": "Diagnostics",
    "認証": "Authentication",
    "資料を検索": "Search documents",
    "起動時に接続を試す": "Try connecting at startup",
    "追加": "Add",
    "送信": "Send",
    "送信先": "Destination",
    "送信許可": "Request permission",
    "選べるAIと、": "Your choice of AI.",
    "選択": "Select",
    "選択しない": "None",
    "選択中": "Selected",
    "グラフを描画できませんでした。元データは全データ欄に保存されています。": "Unable to render the chart. Original data is included in the complete session data.",
  };
  const japanese = {
    "AI connections": "AI接続", "MCP servers": "MCPサーバー", "Connected tools": "接続済みツール",
    "Knowledge": "資料", "MCP Resources / Prompts": "MCPリソース / プロンプト",
    "General workspace": "汎用ワークスペース", "LOCAL HISTORY": "ローカル履歴",
    "CONNECTIONS": "接続", "Provider": "プロバイダー", "Transport": "通信方式",
    "LOCAL ASSISTANT": "アシスタント", "MCP TOOL": "MCPツール",
    "YOUR MODEL · LOCAL TOOLS": "選べるモデル · ローカルツール", "FIRST RUN · CHOOSE YOUR AI": "初回セットアップ · AIを選択",
    "コピー": "コピー",
  };
  english["コピー"] = "Copy";
  let current = "ja";
  try { if (localStorage.getItem("use-lllm-language") === "en") current = "en"; } catch (_) { /* storage may be disabled */ }
  const bindings = [];
  const rendered = new Map();
  function setLanguage(language, persist = true) {
    if (!["ja", "en"].includes(language)) throw new Error("Unsupported language");
    current = language;
    if (persist) { try { localStorage.setItem("use-lllm-language", language); } catch (_) { /* session only */ } }
  }
  function t(source, ...values) {
    const key = typeof source === "string" ? source : source.reduce((text, part, index) => text + part + (index < values.length ? `{${index}}` : ""), "");
    const translated = (current === "en" ? english[key] : japanese[key]) ?? key;
    const result = typeof source === "string" ? translated : translated.replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)]));
    if (known(key)) {
      if (rendered.size > 1000) rendered.clear();
      rendered.set(result, { source, values });
    }
    return result;
  }
  function retranslate(value) {
    const item = rendered.get(value);
    return item ? t(item.source, ...item.values) : t(value);
  }
  function known(value) { return Object.hasOwn(english, value) || Object.hasOwn(japanese, value); }
  function captureStatic(root) {
    // Capture initial UI nodes only. Never traverse later user or model content.
    const walker = document.createTreeWalker(root, 4);
    let node;
    while ((node = walker.nextNode())) {
      if (node.parentElement?.closest("script, style, #session-title, #language-toggle")) continue;
      const source = node.textContent.trim();
      if (known(source)) bindings.push({ node, source, prefix: node.textContent.match(/^\s*/)[0], suffix: node.textContent.match(/\s*$/)[0] });
    }
    root.querySelectorAll("[title], [aria-label], [placeholder], [data-prompt]").forEach((element) => {
      for (const attr of ["title", "aria-label", "placeholder", "data-prompt"]) {
        const source = element.getAttribute(attr);
        if (known(source)) bindings.push({ node: element, attr, source, previous: source });
      }
    });
  }
  function applyStatic() {
    document.documentElement.lang = current;
    for (const item of bindings) {
      if (!item.node.isConnected) continue;
      if (item.attr) {
        if (item.node.getAttribute(item.attr) !== item.previous) continue;
        item.previous = t(item.source);
        item.node.setAttribute(item.attr, item.previous);
      }
      else item.node.textContent = item.prefix + t(item.source) + item.suffix;
    }
  }
  return { t, setLanguage, language: () => current, captureStatic, applyStatic, retranslate, english };
});
