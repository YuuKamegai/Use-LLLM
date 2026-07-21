# Use-LLLM

OllamaのローカルLLMを、Claude Desktopのような会話UIから利用するWindows向けアプリです。
会話と設定はローカルへ保存され、必要なMCPサーバーだけを後から接続できます。

## Windowsへインストール

`dist\Use-LLLM-Setup.exe`を実行します。Python、uv、.NET Runtimeの事前導入は不要です。

- Windows 10/11 x64
- ユーザー単位で`%LOCALAPPDATA%\Programs\Use-LLLM`へインストール
- 会話、Knowledge、設定は`%LOCALAPPDATA%\Use-LLLM`へ保存
- アンインストールしてもローカルデータは既定で保持

初回起動時にOllamaを診断します。未導入の場合は公式Windowsインストーラーへの
リンクを表示し、Ollama起動後に会話モデルを選択または取得します。Ollama本体と
モデルはUse-LLLMインストーラーには含まれません。

MCPサーバーは任意です。初期状態では何も登録せず、「接続と設定」からstdio、
Streamable HTTP、SSEサーバーを追加するか、Claude Desktop設定JSONを取り込みます。

## インストーラーをビルド

開発PCには`uv`、.NET 10 SDK、Node.jsが必要です。PyInstallerはビルド時だけ`uv`が
隔離環境へ取得します。

```powershell
uv sync --dev
.\Build-Windows-Installer.ps1
```

ビルドはpytest、Ruff、JavaScript構文検査を実行し、次を生成します。

```text
dist\Use-LLLM-Setup.exe
```

生成物は、Python 3.12アプリランタイムとself-contained .NETランチャーを内包する
ユーザー単位インストーラーです。配布前のコード署名は別途必要です。

## 開発用起動

```powershell
uv sync --dev
.\Start-WebUI.ps1
.\Start-WebUI.ps1 -NoBrowser
```

または直接起動します。

```powershell
$env:PYTHONPATH = "src"
uv run python -m use_lllm serve --open
```

GUIランチャーだけを再ビルドする場合は`Build-WebUI-Launcher.ps1`を使用します。

## 品質チェック

```powershell
uv run pytest -q
uv run ruff check src tests
node --check src/use_lllm/general/static/app.js
node tests/test_general_markdown.cjs
python -m compileall -q src
git diff --check
```

## 構成

- `src/use_lllm/general/`：チャットAPI、エージェントループ、WebUI
- `src/use_lllm/core/`：設定、Ollama、MCP、セッション、初回セットアップ
- `launcher/`：開発・インストール共用GUIランチャー
- `installer/`：ユーザー単位Windowsインストーラー
- `Build-Windows-Installer.ps1`：self-contained配布物の再現可能ビルド

Knowledgeは現在、選択資料を会話へ手動添付するローカル機能です。引用付きRAG、
埋め込み検索、PDF/Office抽出は後続実装です。
