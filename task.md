# Use-LLLM desktop experience expansion

## Goal

Claude DesktopのようにローカルLLMと会話でき、GUIだけでMCPと知識を扱える体験へ拡張する。
既存のloopback運用、SQLiteセッション、stdio MCP、承認ゲート、ms-data-parser互換は維持する。

## Scope approved on 2026-07-15

- [x] 真のトークンストリーミング、Markdown/コード表示、ツール進捗、中断・エラー復帰
- [x] MCP接続ウィザード、環境変数、プリセット、Claude設定JSON import、診断表示
- [x] Streamable HTTP/SSE、OAuth、Resources、Resource Templates、Prompts
- [x] 多言語ツール検索、会話単位のツール有効化、汎用MCPコンテンツ表示
- [x] ファイル添付とKnowledgeワークスペース

## Acceptance criteria

- stdio MCPの既存設定・接続・ツール実行テストが後方互換で通る。
- WebUIで応答delta、ツール開始/完了、承認待ち、完了を逐次表示できる。
- MarkdownはHTMLを直接信頼せず、安全にコード・リンク・表・リストを描画する。
- MCP設定でstdio/Streamable HTTP/SSEを選べ、stdioではenv、HTTPではOAuthを指定できる。
- MCPのtools/resources/resource templates/promptsを接続診断とUIで確認できる。
- 24件を超えるツールでも内部検索を使って候補を更新でき、日本語説明も検索対象になる。
- 会話ごとに不要なツールを無効化できる。
- text/image/resource/structured contentを汎用表示し、PCA/EICレンダラーを維持する。
- ローカルファイルをKnowledgeへ保存し、選択した資料を次の会話へ添付できる。
- 中断・例外後にセッションがrunningのまま残らない。
- 全pytest、JS構文、Node単体テスト、compileall、git diff check、ライブHTTP/browser smokeが通る。

## Non-goals for this slice

- クラウドLLMプロバイダー対応
- 自動RAG回答生成と再ランキング（Knowledge検索・添付基盤まで）
- OSインストーラーの再設計
- 複数ユーザー認証

## Verification

- `uv run pytest -q`: 94 passed
- `uv run ruff check src tests`: passed
- `node --check`（app/markdown/artifact renderer）: passed
- `node tests/test_general_markdown.cjs`: passed
- `python -m compileall -q src`: passed
- 一時ポート8766のHTTP smoke: index/static/Knowledge/Resources/Prompts すべて200
- in-app browserは、このセッションの既存ブラウザタブが終了済みのため未実施

## Launcher dynamic port fix (2026-07-15)

- [x] EXEから`127.0.0.1:8765`の固定probe/openを除去
- [x] 既定で空きloopbackポートを選択し、`-Port`でPowerShell/Pythonへ伝播
- [x] OAuth callback URIにも実際のWebUIポートを伝播
- [x] ランチャーを再ビルドし、EXEの実起動で8765へアクセスしないことを確認

実起動確認: 新EXEは`58337`を選択し、親PowerShell・Pythonへ同じポートを伝播。`launcher-health`は200相当、8765のListenなし。検証プロセスは終了済み。

## Windows installer and first-run setup (approved 2026-07-15)

### Product boundary

- [x] Python/.NET未導入のWindows 10/11 PCへユーザー単位でインストールできる。
- [x] Use-LLLMはClaude Desktop風の汎用ローカルLLMチャットとして配布する。
- [x] Ollamaは同梱せず、初回セットアップから公式Windowsインストーラーを案内する。
- [x] チャットモデルを初回セットアップで選択・取得し、疎通後に完了状態を保存する。
- [x] 新規環境ではMCPサーバーを0件で開始し、必要な外部MCPをユーザーが後から追加できる。
- [x] 既存settings.jsonのMCP設定はアップグレード後も保持する。
- [x] アンインストール時は会話・Knowledge・設定を既定で保持する。

### Deliverables

- [x] self-contained Windowsアプリペイロード
- [x] self-contained GUIランチャー
- [x] `Use-LLLM-Setup.exe` インストーラー
- [x] 初回セットアップUIとOllama/model診断API
- [x] インストール、上書き更新、アンインストールの検証

### Deferred

- 引用付きRAG、埋め込み、再ランキング、Office/PDF抽出
- Ollama本体またはモデルのインストーラー内同梱
- 特定MCPサーバー（ms-data-parserを含む）の既定登録・同梱
- コード署名証明書の取得

### Verification

- `uv run pytest -q`: 102 passed
- `uv run ruff check src tests`: passed
- `node --check src/use_lllm/general/static/app.js`: passed
- `node tests/test_general_markdown.cjs`: passed
- `python -m compileall -q src`: passed
- launcher/installer .NET build: 0 warnings, 0 errors
- packaged runtime HTTP smoke: launcher-health/static/setup 200相当、fresh MCP count 0
- quiet install: runtime/launcher/uninstaller/Start Menu/registryを確認
- installed launcher smoke: 動的loopbackポートでstatic ready、初回setup未完了、MCP任意
- quiet uninstall: program/registry/shortcut削除、`%LOCALAPPDATA%\Use-LLLM`保持
- artifact: `dist\Use-LLLM-Setup.exe`、SHA256 `52B6E8106DBBCB5CD5C591DB00B32957607ECDC4D05C2A176D915048C4523123`
- Authenticode: `NotSigned`（配布署名前の既知残件）

## Lipidmix MCP生成PNGのチャット内表示（2026-08-07）

- [x] `save_pca_figure`、`save_volcano_figure`、`save_eic_figure`のテキスト結果からWindows絶対PNGパスを検出する。
- [x] PNGシグネチャ、20 MiB上限、最大4画像を検証し、セッション専用artifact領域へコピーする。
- [x] 元の絶対パスをWebUIへ渡さず、同一オリジンのセッションartifact APIからPNGだけをinline配信する。
- [x] MCP標準image blockとの後方互換を維持しつつ、保存PNGをツール結果内へ遅延表示し、原寸リンクを付ける。
- [x] セッション再読込後も、保存済みtool messageのcontent blockから画像を再表示する。

### Verification

- 対象Pythonテスト: 30 passed（既知のStarletteDeprecationWarning 1件）
- 全Pythonテスト: 125 passed / 8 subtests（既知のStarletteDeprecationWarning 1件）
- artifact renderer Nodeテスト: passed
- Markdown / PCA / EIC / artifact renderer Nodeテスト: passed
- Ruff / format check / compileall / uv lock / JavaScript構文検査 / `git diff --check`: passed
- 実ブラウザ: セッション再読込後のtool message内でPNGを正常ロード、セッションartifact URL、lazy load、原寸リンク、console warning/error 0件を確認
- Windowsインストーラー再ビルド: `dist\Use-LLLM-Setup.exe`、SHA256 `2277F842B90CAF723F36C06253B2DE59AECD726105B718B8CBA1B9AEBF32EC3A`
- packaged runtime smoke: `launcher-health.static_ready=true`、artifact renderer 200、`artifact_image`と同一オリジンURL検証コードの同梱を確認
- Authenticode: `NotSigned`（配布署名前の既知残件）

## セッション残りコンテキスト表示（2026-07-15）

- [x] 入力欄フッターにセッション固有の残りコンテキスト率を表示する。
- [x] Ollamaの`prompt_eval_count`と保存する応答の推定量から使用量を算出する。
- [x] モデル未ロード時は未計測表示とし、初回応答後に`/api/ps`の割当コンテキスト長を再取得する。
- [x] 既存の自動要約しきい値（入力65%）までの実用残量をセッション状態へ保存する。
- [x] 40%未満を警告色、15%未満を危険色で表示し、詳細をツールチップで確認できる。
- [x] デスクトップ幅と390px狭幅の実ブラウザ表示を確認する。

### Verification

- `uv run pytest -q`: 102 passed
- `uv run ruff check src tests`: passed
- `uv run python -m compileall -q src tests`: passed
- `node --check`（General WebUI JavaScript一式）: passed
- `node tests/test_general_markdown.cjs`: passed
- `git diff --check`: passed
- 実Ollama/browser smoke: qwen3:14b、割当4.10k、実用入力予算2.66k、残量93%を表示
- Windowsインストーラ再ビルド: ペイロードとインストール済みランタイムに`context-meter`／`context_usage`を確認
- quiet install/runtime HTTP smoke/quiet uninstall: 成功、検証前の未登録状態へ復元

## 数式表示と新規チャット名（2026-07-22）

- [x] KaTeXをローカル静的資産として同梱し、`$$...$$`、`\\[...\\]`、`\\(...\\)`を安全に描画する。
- [x] コードブロック内のTeXは数式化せず、不正な数式やKaTeX未読込時は安全なテキストへフォールバックする。
- [x] 「新しいチャット」の初回ユーザー入力を空白正規化し、先頭38文字をセッション名として保存する。
- [x] 明示済みタイトルを保持し、ストリーミング開始時に確定タイトルを画面へ即時反映する。
- [x] KaTeXのJS/CSS/WOFF2フォントとMITライセンスをwheelおよびWindowsアプリの収集対象に含める。

### Verification

- `uv run pytest tests -q`: 106 passed（既知のStarletteDeprecationWarning 1件）
- `uv run ruff check src tests`: passed
- `uv run ruff format --check`（変更Pythonファイル）: passed
- General WebUI JavaScript全件の`node --check`: passed
- `node tests/test_general_markdown.cjs`、PCA/EIC helper tests: passed
- `uv run python -m compileall -q src tests`: passed
- `uv lock --check`: passed
- wheel内のKaTeX JS/CSS/代表フォント/ライセンス: confirmed
- Windowsインストーラー再ビルド: `dist\\Use-LLLM-Setup.exe`、SHA256 `194EFB17F147C3627F5ACC0FD364EFDA248987630EE3A7BFA5D7A1104496D84D`
- packaged runtime smoke: `launcher-health.static_ready=true`、KaTeX JS/CSS/代表フォント200、修正版Markdown/タイトル処理を確認
- in-app browser: 利用可能なブラウザインスタンスが0件のため未実施

## Azure OpenAI接続とローカルMCP連携（2026-08-07）

- [x] CONNECTIONSでOllama / Azure OpenAIを選択し、Endpoint、Deployment、API keyを設定できる。
- [x] Azure portalのresource endpointと`/openai/v1`付きURLを同じv1 Chat Completions URLへ正規化する。
- [x] API keyをローカル設定へ保存し、公開設定APIと編集フォームへ値を再表示しない。
- [x] Azure送信先を`*.openai.azure.com`と`*.services.ai.azure.com`へ限定し、任意HTTPSホストへのキー送信を拒否する。
- [x] Providerまたは実際のEndpoint変更時は保存済みAPI keyを引き継がず、再入力を必須にする。
- [x] loopback WebUIへHost検証、same-origin検証、起動単位CSRFトークンを追加する。
- [x] Azure API keyをWindows DPAPI CurrentUserで暗号化し、旧平文設定を読み込み時に自動移行する。
- [x] Azureのtool call名制約に合わせて`server::tool`を送信時だけ安全な名前へ変換し、応答時に元へ戻す。
- [x] Azureのストリーミングtool call断片を結合してから、既存の承認・ローカルMCP実行・状態復元ループへ渡す。
- [x] ローカルMCPの実行結果とAzureの`tool_call_id`を次のAzure要求へ返し、最終応答まで継続する。
- [x] 初回画面からOllamaを必須にせず、Azure OpenAIのCONNECTIONS設定へ進める。
- [x] READMEへ設定手順、ローカルMCP境界、Azureへ送信される情報、API key保存上の注意を記載する。
- [x] GPT-5.4系Azureモデルの総コンテキスト・入力・出力上限をモデル別に解決する。
- [x] Azure接続ごとに任意のコンテキスト上限を設定し、Ollamaの動的検出と分離する。
- [x] ストリーミング要求で`include_usage`を有効化し、prompt/completion使用量を実測する。
- [x] 上限未確認時は残量表示を暫定値として明示する。

### Verification

- `uv run pytest tests -q`: 122 passed / 8 subtests（既知のStarletteDeprecationWarning 1件）
- `uv run ruff check src tests`: passed
- `uv run ruff format --check`（変更Pythonファイル）: passed
- `node --check src/use_lllm/general/static/app.js`: passed
- Markdown/PCA/EIC JavaScript helper tests: passed
- `uv run python -m compileall -q src tests`: passed
- `uv lock --check`: passed
- `git diff --check`: passed
- Azure互換HTTPモック: API key header、MCP名変換、tool call ID、ストリーミング断片結合を確認
- Azure contextモック: GPT-5.4 miniの400k上限、260k入力予算、usage-only最終chunkを確認
- agent-loop統合テスト: Azure tool call → ローカルMCP実行 → MCP結果をAzureへ返却 → 最終応答を確認
- in-app browser: 起動単位トークン埋め込み、Azure設定POST、空のEndpoint/Deployment欄、password型API key欄、console warning/error 0件を確認
- Windowsインストーラー再ビルド: `dist\Use-LLLM-Setup.exe`、SHA256 `9261F73205759B20DA7115A7212549471BD5C6481C79BC72843AA4C7E98464ED`
- packaged runtime context smoke: launcher-health、Azure上限欄、暫定値表示コード、モデルプロファイル同梱を確認
- packaged runtime security smoke: 未信頼Host=400、tokenなしPOST=403、cross-origin POST=403、任意Azure送信先=400、DPAPI envelope保存、平文API keyなし、公開API keyなしを確認
- Authenticode: `NotSigned`（配布署名前の既知残件）

## 2026-08-07 全プロットのクライアント描画統一（volcano新設 / EIC multi対応）

- [x] `volcano-plot.js` 新設。`lipidmix.volcano.v1` を sig ごと3トレースで描画、しきい値を破線に、
      hover に feature 名。配色は `save_volcano_figure` の matplotlib と一致（up `#c0392b` /
      down `#2471a3` / ns `#95a5a6`）。
- [x] `eic-plot.js` を single/multi 2スキーマ対応に。`lipidmix.eic.multi.v1` がスキーマ判定で
      弾かれ、複数物質オーバーレイが webUI に描画されず PNG 保存に頼るしかなかった問題を解消。
      multi では物質メタを hovertemplate に焼き込み、apex 注釈を返す。
- [x] `app.js` に `appendVolcanoPlot` を配線。EIC は 8 系列超で凡例を右外側の縦並びに切替。
- [x] `policy.py` / `mcp_state_policy.py` を ms-data-parser の実ツール名へ同期。旧名 `eicaef_*` の
      ままで現行 EIC ツールと `arf_plot_volcano` が UNKNOWN 扱い＝毎回承認待ちになり描画に
      到達できなかった。撤去済み `arf_re_pca` / `pai2_get_top_metabolites` 等も削除。
- [x] `arf_differential` は群指定引数依存のため `replay_safe` にせず、`differential_result` を
      provides するだけに。`arf_plot_volcano` の自動状態復元は `StateRestoreBlocked` になる。

### Verification

- `.venv\Scripts\python.exe -m pytest -q`: 134 passed / 8 subtests（既知のStarletteDeprecationWarning 1件）
- `.venv\Scripts\python.exe -m ruff check src tests`: passed
- `node --check`（app.js / eic-plot.js / volcano-plot.js）: passed
- Markdown / PCA / EIC / volcano / artifact JavaScript helper tests: passed
- 実データ検証（`C:\Users\yuu18\datasets\2_lipidome_lcms\NEG`, 714特徴 / 60サンプル）:
  - サーバ側ペイロード生成 → クライアント JS ヘルパ通し: volcano 708点(ns633/down50/up25)＋破線3本、
    EIC single 4トレース610点、EIC multi 12トレース2279点＋注釈12 を確認
  - webUI 実描画（Plotly SVG 生成を確認）: volcano / EIC single / EIC multi の3枚
  - `arf_plot_volcano` 実行では PNG が0枚、`save_volcano_figure` の明示呼び出し時のみ
    `reports/figures/verify-volcano_volcano.png`（38,751 bytes）が作られることを確認
  - MCP ツール数 39→40（`arf_plot_volcano` 登録）
- 実データ検証で発覚し修正: 図の説明文が「間引き」と「検定不能で除外」を混同していた
  （`ns_plotted == ns_total` で間引き無しなのに「ns を間引き」と表示）。`note()` を
  `volcano-plot.js` へ移してテストで固定。
