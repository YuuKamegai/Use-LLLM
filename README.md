# Use-LLLM

OllamaのローカルLLMまたはAzure OpenAIを、Claude Desktopのような会話UIから利用するWindows向けアプリです。
会話と設定はローカルへ保存され、必要なMCPサーバーだけを後から接続できます。

## Windowsへインストール

`dist\Use-LLLM-Setup.exe`を実行します。Python、uv、.NET Runtimeの事前導入は不要です。

- Windows 10/11 x64
- ユーザー単位で`%LOCALAPPDATA%\Programs\Use-LLLM`へインストール
- 会話、Knowledge、設定は`%LOCALAPPDATA%\Use-LLLM`へ保存
- アンインストールしてもローカルデータは既定で保持

初回起動時にOllamaを診断します。未導入の場合は公式Windowsインストーラーへの
リンクを表示し、Ollama起動後に会話モデルを選択または取得できます。Azure OpenAIを
使う場合は「Azure OpenAIを設定」からCONNECTIONSを開きます。Ollama本体とモデルは
Use-LLLMインストーラーには含まれません。

MCPサーバーは任意です。初期状態では何も登録せず、「接続と設定」からstdio、
Streamable HTTP、SSEサーバーを追加するか、Claude Desktop設定JSONを取り込みます。
`Lipidmix_with_LLM`の`save_pca_figure`、`save_volcano_figure`、`save_eic_figure`が
PNGを保存した場合は、安全性を確認してセッション領域へコピーし、ツール結果の直下に表示します。
画像をクリックすると原寸表示できます。元ファイルの絶対パスをブラウザーへ直接公開しません。

## Azure OpenAIを使う

「接続と設定」→「AI connections」→「追加」でProviderにAzure OpenAIを選び、次を入力します。

- Endpoint：Azure portalに表示される`https://<resource>.openai.azure.com`、または
  `https://<resource>.services.ai.azure.com`
- Deployment：Azureへデプロイしたチャットモデルのdeployment名（モデル名ではありません）
- API key：Azure OpenAIリソースのキー
- コンテキスト上限：任意。既知モデルは自動判定し、独自deployment名で判定できない場合だけ入力

保存後に「接続テスト」を実行し、成功した接続を「選択」します。Endpointは
`/openai/v1`付きでも入力でき、内部でAzure OpenAI v1 Chat Completions URLへ正規化します。
上記のAzure公式ホスト以外は拒否します。保存後にProviderまたは実際のEndpointを変更する
場合は、以前の送信先のキーを引き継がず、API keyの再入力が必要です。
GPT-5.4 miniなどの既知モデルではAzureのモデル上限を自動適用します。任意入力した上限は
その接続だけに適用されるため、Ollamaへ切り替えても引き継がれません。

ローカルMCPサーバーは引き続きこのPCで起動・実行されます。AzureモデルがMCP tool callを
返すとUse-LLLMがローカルMCPを呼び、その結果を次のAzure OpenAIリクエストへ含めます。
したがって、プロンプトだけでなく、モデルへ読ませるMCPツール定義と実行結果もAzureへ
送信されます。既存のread-only自動実行、承認ゲート、セッション別MCP状態復元は維持されます。

API keyはWindows DPAPIのCurrentUserスコープで暗号化してからローカルの`settings.json`へ
保存し、`/api/settings`やWebUIへ値を返しません。旧版の平文API keyは、同じWindows
ユーザーで初回に読み込んだとき自動的に暗号化形式へ移行します。暗号化されたキーは通常、
同じWindowsユーザーかつ同じPCでのみ復号できます。

WebUIサーバーはloopbackへだけbindし、Hostを`127.0.0.1`、`localhost`、`::1`へ限定します。
設定変更やチャットなどの更新APIには、起動ごとに生成するトークンとsame-origin検証が必要です。

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
node tests/test_general_artifacts.cjs
python -m compileall -q src
git diff --check
```

## 構成

- `src/use_lllm/general/`：チャットAPI、エージェントループ、WebUI
- `src/use_lllm/core/`：設定、Ollama、Azure OpenAI、MCP、セッション、初回セットアップ
- `launcher/`：開発・インストール共用GUIランチャー
- `installer/`：ユーザー単位Windowsインストーラー
- `Build-Windows-Installer.ps1`：self-contained配布物の再現可能ビルド

Knowledgeは現在、選択資料を会話へ手動添付するローカル機能です。引用付きRAG、
埋め込み検索、PDF/Office抽出は後続実装です。
