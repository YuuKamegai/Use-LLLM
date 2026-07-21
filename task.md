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
