# Use-LLLM WebUI（汎用チャット）

ローカル Ollama と `ms-data-parser` MCP を対象にした loopback 限定の汎用チャット WebUI。
`Lipidmix_with_LLM/server.py` を既定 MCP サーバとして起動する。

## 起動

```powershell
cd C:\Users\yuu18\Use-LLLM
.\Start-WebUI.ps1            # http://127.0.0.1:8765/general/ を開く
.\Start-WebUI.ps1 -NoBrowser # ブラウザを開かず起動
```

または直接:

```powershell
cd C:\Users\yuu18\Use-LLLM
$env:PYTHONPATH = "src"
python -m use_lllm serve --open
```

GUI ランチャ（`Use-LLLM-WebUI.exe`）は `Start-WebUI.ps1` を起動して同 URL を開く。
再ビルド: `.\Build-WebUI-Launcher.ps1`（.NET 10 SDK 必要）。
同梱の `Use-LLLM-WebUI.exe` は framework-dependent ビルドのため、実行には .NET 10 Desktop Runtime のインストールが必要（SDK は再ビルド時のみ必要）。

## 開発環境

開発依存を含む仮想環境を `uv` で同期する。

```powershell
cd C:\Users\yuu18\Use-LLLM
uv sync --dev
```

## 品質チェック

```powershell
uv run ruff check .
uv run ruff format --check .
```

Ruff の安全な自動修正とフォーマットを適用する場合:

```powershell
uv run ruff check . --fix
uv run ruff format .
```

## テスト

```powershell
cd C:\Users\yuu18\Use-LLLM
uv run pytest tests -v
```

## 構成

- `src/use_lllm/general/` … 汎用チャット本体（API・エージェントループ・静的 UI）
- `src/use_lllm/core/` … 共有基盤（設定・MCP・ポリシー・セッション）
- `src/use_lllm/app.py` … `/general` に汎用をマウントする単体アプリ
- 実行時データは `%LOCALAPPDATA%/Use-LLLM/`（sqlite・settings.json・sessions）
- 起動スクリプトとEXEは、このリポジトリ直下の `src` と `Start-WebUI.ps1` を解決し、移行前の配置には依存しない。
- 既定の `ms-data-parser` MCPだけは、移動対象外の `C:\Users\yuu18\Lipidmix_with_LLM\server.py` を意図的に使用する。`USE_LLLM_MCP_SERVER_SCRIPT` で上書きできる。
