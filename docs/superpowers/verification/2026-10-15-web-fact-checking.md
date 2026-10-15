# Web検索・事実確認の検証記録

2026-10-15 / Windows / 隔離worktree・専用venv。元の保存設定・実データ・会話履歴は変更していない。

## 自動検証

- 既存baseline: 191 passed, 14 subtests。
- 実装後: `uv run --frozen python -m pytest -q`: 260 passed, 14 subtests。
- 既存のStarlette TestClient非推奨警告1件あり。baselineから存在し、本変更の失敗ではない。
- `ruff check .`、`uv lock --check`、`node --check`（app.js/i18n.js）、`git diff --check`: 成功。
- 取得IP固定・TLSホスト維持、非公開IP/DNS混在拒否、容量・展開後容量・期限、robots/redirect、
  有限検索worker、設定・接続保持、両APIアダプターの通常/stream、権限・回数・失敗表示を検証。
- 各TaskのRED→GREENを確認。実通信や実モデルの合否をこの件数へ混ぜていない。

## 公開ネットの実通信

`tools/web_smoke.py --query "Python documentation pathlib" --url "https://docs.python.org/3/library/pathlib.html"`

- DDGS 9.16.0 / primp 2.0.1 / Trafilatura 2.3.1。
- DuckDuckGo固定で5結果、Python公式ページのタイトル・本文・最終URL・取得日時を取得できた。
- 長い本文は12,000文字で省略され、truncated=trueとなった。
- 検索は一時的にDDGSExceptionとなることも実モデル評価中に観測。別backendへの黙った切替はしていない。

## 実モデル評価

新規の一時SessionStoreを利用し、MCPは内蔵Web2件だけ。公開情報のみを送信した。
ローカルはOllama `qwen3.5:9b-q4_K_M`、APIは設定済みAzure OpenAIのdeploymentを使用した。
各応答最大800生成token、各ケース180秒、最大6ステップという評価用の上限を設けた。
この上限は製品の20ステップ上限を変更しない。

| ケース | Azure | ローカル |
|---|---|---|
| 最新情報と公式根拠 | search→read_page→公式URL付き回答 | 初回は検索4回失敗後に誤断定。対策後の再試行はsearch2回→read_page→公式URL付き回答 |
| 架空の廃止発表の確認 | search2回後に未確認と回答 | search3回→read_page。800token制限下で最終回答が空になった |
| 短い翻訳 | Web呼出し0回、翻訳 | Web呼出し0回、翻訳 |
| 「検索しないで提示文だけ」 | Web呼出し0回、言換え | Web呼出し0回、言換え |

検索結果・取得本文の解釈や引用の品質はモデルに依存する。上記は少数のsmoke評価であり、
全質問での正確性、ChatGPTと同一品質、検索判断の網羅性を証明しない。

実評価で見つけた失敗への対策:

- Web確認が全件失敗した場合、モデルの未確認の断定を回答として表示せず、
  アプリ側で「根拠を確認できなかった」と返す。通常/stream両経路でRED→GREEN。
- Web実行後にモデルの最終回答が空の場合は、生成できなかった旨を表示する。
- DDGSが明示するtimeout/rate limitと一般失敗を区別する。

## UI確認

一時保存先・合成モデル・合成MCPで `tests.general_ui_fixture` を起動した。
元のアプリを検証対象にせず、日英切替、Web欄の上部表示、オンの保存・再読込、オフ、
2ツールの表示、自動検索と本文取得、通常MCPの承認待ち、承認後の再開と出典リンクを確認。
通常MCPはWeb有効化後も個別承認を要求し、接続は保持された。
保存API失敗時のswitch復帰とエラー表示は、実JSハンドラーをNodeで実行して検証した。

## 計画からの判断

- HTTPXの薄いtransportではなくhttpcoreを直接使用。Cookie/proxy/展開の経路を減らし、
  同じIP固定・Host/SNI維持の要件を満たす。
- fresh venvではtestsパッケージ参照のため`uv run python -m pytest`を使う。
- 小さいHTML fixtureと結果契約のテストは関連テストファイル内へ配置。
- Web設定だけの変更は既存registryを包む/外す処理とし、MCP接続の作り直しを避ける。
- 検索失敗の最終回答ガードは、実モデルの誤断定を受けて追加した。

生成した画面画像と公開情報だけの実評価ログはworktreeのignored `artifacts/`に保存。
認証キーや元の会話本文は検証記録へ含めていない。
