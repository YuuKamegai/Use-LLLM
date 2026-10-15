# Web Fact-Checking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OllamaとAzure OpenAIが、APIキー不要のWeb検索・公開ページ本文確認を必要に応じて使い、出典付きで回答できるようにする。

**Architecture:** Use-LLLM内蔵の`web::search`と`web::read_page`を、既存MCPレジストリを包むアダプターで公開する。Web有効化が個別承認省略の根拠となり、通常のMCP権限は変更しない。両LLMで共通のinstructions、実行回数制限、履歴、SSE表示を使用する。

**Tech Stack:** Python 3.11以上、既存FastAPI/httpx/MCP、httpcore 1.x、DDGS、Trafilatura、既存JavaScript UI、pytest/unittest。

**Spec:** [2026-10-15-web-fact-checking-design.md](../specs/2026-10-15-web-fact-checking-design.md)（本文承認済み）。

## Global Constraints

- `Settings`へ`web_search_enabled: bool = False`を追加する。
- 初期バックエンドは明示的に`duckduckgo`を指定する。
- 入力: `query`（1〜500文字）、`max_results`（1〜10、既定5）。
- 既定regionは`jp-jp`、SafeSearchは`moderate`とする。
- タイトル300文字、概要1000文字まで。
- 入力: `url`（HTTP(S)、最大2048文字）。取得本文は最大12,000文字。
- 1ユーザー発話からの実行上限は検索4回、本文取得6回とする。
- 既存の20ステップ上限も維持する。承認後の再開でWeb回数をリセットしない。
- 本文取得はHTTP(S)の80/443ポートのみ。リダイレクトは最大3回。
- 応答はストリームで上限2MiB（展開後も検査）。
- 検索は1呼び出し30秒、本文取得はrobots・リダイレクト・DNSを含め合計30秒まで。
- SSL検証を無効にしない。Cookie、ユーザーのログイン情報、LLM APIキーをページ先へ送らない。
- 決定的な自動テストはネットに依存させず、通信とモデルは置き換える。
- ms-data-parser、実データ、既存チャット本文と実際の保存済み設定は変更しない。
- 実装方法の選択と計画レビュー完了までは製品コード・依存環境を変更しない。

## Review Focus

1. AI接続・MCP設定を更新してもWebの有効状態が消えない（Task 5）。
2. DNSの複数回答やリダイレクト、接続直前の宛先変化で内部ネットへ到達しない（Task 1/2）。
3. 検索のキャンセル・タイムアウト後にもワーカーが増殖せず、別チャットを塞がない（Task 3）。
4. 外部MCPの`web`名衝突やツール名省略で、承認省略・セッション無効化を迂回しない（Task 4/6）。
5. 自動検索を有効化しても、検索失敗を成功と扱わず、翻訳や「検索しない」の指示を尊重する（Task 6/7）。

## 実行前の準備とファイル構成

現在のブランチは`codex/web-fact-checking`。実行開始時にusing-git-worktreesスキルで作業場所を確認し、
既存の適切なチェックアウトまたは添付worktreeを優先する。新規worktreeの場合はこの設計・計画のコミットを含める。
AGENTS.mdとKBの指示を再確認し、起動中の実アプリを検証用サーバーに流用しない。

新設するファイルの責務:

| ファイル（`src/use_lllm/`基準） | 責務 |
|---|---|
| `web/__init__.py` | パッケージ入口。importだけで通信・worker起動しない |
| `web/contracts.py` | 結果型・エラー・schema・MCP結果への変換 |
| `web/http.py` | URL/DNS検証、接続IP固定、HTTPストリーム、時間と容量制限 |
| `web/pages.py` | robots判定、リダイレクト、HTML/テキスト本文抽出 |
| `web/search.py` | DDGS呼出し、結果正規化、有限数の同期worker管理 |
| `web/registry.py` | 信頼済み内蔵ツールと既存MCPの合成 |
| `web/instructions.py` | 検索要否・一次資料・引用・非信頼本文の扱い |
| `web/budget.py` | セッション状態に保存する発話単位の回数管理 |

既存変更先: `core/settings_store.py`, `general/api.py`, `general/agent_loop.py`,
`general/static/{index.html,app.js,i18n.js,styles.css}`, `pyproject.toml`, `uv.lock`, `README.md`。
`core/policy.py`と通常MCPの承認規則を変更しない。

検証コマンドは作業ルートからPowerShellで実行する。以下の`uv run --frozen`は承認後に
選択した環境で実行し、稼働中アプリが使うvenvへの同時変更を避ける。

---

### Task 1: 結果契約と公開ネット専用のHTTP取得

**Files:** Create `src/use_lllm/web/{__init__,contracts,http}.py`; Test `tests/test_web_http.py`, `tests/test_web_contracts.py`; Modify `pyproject.toml`, `uv.lock`。

**Interfaces:**
- `WebResult(payload: dict[str, Any], is_error: bool)`（frozen dataclass）と`to_mcp_result(name: str, result: WebResult) -> MCPToolResult`。
- `WebError(code: str, message: str)`例外。`error`は常に`{code, message}`または`None`。
- `normalize_url(url: str) -> str`: fragment除去、ホスト小文字化・IDNA、標準ポート正規化。クエリの意味や順序は変更しない。
- `async resolve_public_url(url: str, *, deadline: float) -> ResolvedTarget`。`ResolvedTarget`は正規化URL・元hostname・port・検証済みIP群を保持する。
- `async SafeHTTPClient.get(url: str, *, deadline: float) -> FetchedResponse`。本文サイズ上限2MiB、自動redirectなし。`FetchedResponse`は`url`, `status_code`, `headers`, `body: bytes`。
- 接続境界は`PinnedNetworkBackend.connect_tcp(host, port, timeout, local_address, socket_options)`とし、検証したIPを下位backendへ渡す。元hostnameのHTTP HostとTLS SNIを保持する。

- [ ] **RED:** 次のテストを追加する。通信は偽DNSと偽network streamへ差し替える。
  - `test_rejects_non_public_targets`: localhost/private/link-local/multicast/reserved/IPv4-mapped IPv6、userinfo、非HTTP(S)、非標準ポート、2049文字URLを`blocked_url`で拒否し、connect呼出し0件。
  - `test_rejects_mixed_dns_answers`: `93.184.216.34`と`127.0.0.1`の混在を拒否。
  - `test_pins_address_but_keeps_host_and_sni`: 検証後にDNSを内部IPへ変えても接続は検証済みIP、Host/SNIは元hostname、証明書検証有効。
  - `test_limits_decoded_and_wire_body`: Content-Lengthなし、虚偽値、gzip展開後2MiB超を拒否。正常終了・例外・キャンセルでstreamがcloseされる。
  - `test_deadline_covers_dns_and_connection`: DNSで残予算を使った後に新たな30秒を与えない。
  - `test_no_proxy_or_cookie_or_auth_forwarding`: proxy環境変数やSet-Cookieが後続リクエストの宛先・headersに影響しない。
  - `test_result_preserves_failure`: `is_error=True`と構造化エラーがMCP結果でも保持される。
- [ ] **RED確認:** `uv run --frozen pytest tests/test_web_http.py tests/test_web_contracts.py -q`。未実装モジュールまたは期待する挙動の欠如で失敗することを確認。
- [ ] **GREEN実装:** `uv add 'httpcore>=1.0.9,<2'`。ローカルで確認済みのhttpcore 1.0.9の`AsyncConnectionPool(network_backend=...)`契約を使う。HTTPX transportを薄く実装してこのpoolへ委譲し、元URLのままTLSを開始する。上位clientは`trust_env=False`, `follow_redirects=False`、共有cookie jarなし。raw streamと展開後streamの両方を計数し、上限前に全量読込しない。gzip/deflateは残容量を指定した段階的展開とし、decoderが無制限の展開バッファを先に確保する構成を避ける。他のContent-Encodingは明示的に非対応とする。DNSも合計deadline内に含める。
- [ ] **GREEN確認:** 上記コマンドが全PASS。`uv run --frozen ruff check src/use_lllm/web tests/test_web_http.py tests/test_web_contracts.py`も成功。
- [ ] **レビュー地点:** connectへ渡るIPとTLSへ渡るhostnameをテストの記録で照合する。
- [ ] **Commit:** `git add src/use_lllm/web tests/test_web_http.py tests/test_web_contracts.py pyproject.toml uv.lock` → `git commit -m 'feat(web): add bounded public HTTP transport'`。

### Task 2: robotsを尊重した本文取得

**Files:** Create `src/use_lllm/web/pages.py`, `tests/test_web_pages.py`, `tests/fixtures/web/article.html`; Modify `pyproject.toml`, `uv.lock`。

**Interfaces:** Consumes Task 1。`PageReader(http: SafeHTTPClient)`と`async PageReader.read_page(url: str) -> WebResult`。
User-Agentは`Use-LLLM-Web/0.1`。`read_page`の結果fieldsはspec §4のとおりとする。

- [ ] **RED:** `test_extracts_html_title_text_and_timestamp`, `test_plain_text_and_truncation`で本文12,000文字・truncated・最終URL・UTC ISO日時をassertする。公開日なしでは`published_at`を作らない。
- [ ] **RED:** `test_checks_robots_before_body`（Allow/Disallow/404/取得失敗）、`test_revalidates_redirect_destinations`（公開先→内部先、異なる公開originではrobots再確認）、`test_redirect_limit_includes_robots_redirects`を追加する。本文redirectは3回まで、robots自身も3回までで、すべて同じ30秒deadlineを使う。
- [ ] **RED:** `test_rejects_pdf_empty_and_oversized_pages`, `test_untrusted_html_remains_data`, `test_auth_failures_are_not_no_results`を追加する。401/403、制限を示す429、非HTML、偽のJS命令を検証する。
- [ ] **RED確認:** `uv run --frozen pytest tests/test_web_pages.py -q`で未実装の失敗を確認。
- [ ] **GREEN実装:** `uv add trafilatura`。HTMLは`bare_extraction`でtitle/text/dateを抽出し、commentsを除く。プレーンテキストは取得charsetでdecodeする。robotsは`urllib.robotparser`で判定し、HTTP取得は必ずTask 1へ通す。タイムアウト・制限・拒否・非対応・空本文をspecのerror.codeへ写像する。
- [ ] **GREEN確認:** `uv run --frozen pytest tests/test_web_http.py tests/test_web_pages.py -q`が全PASS。
- [ ] **レビュー地点:** robotsやredirect処理に別HTTP経路・新しいdeadlineがないことを確認。
- [ ] **Commit:** 作成ファイルと依存2ファイルのみをstageし、`git commit -m 'feat(web): extract public page text with robots checks'`。

### Task 3: DDGS検索と有限worker

**Files:** Create `src/use_lllm/web/search.py`, `tests/test_web_search.py`; Modify `pyproject.toml`, `uv.lock`。

**Interfaces:** `SearchService()`、`async search(query: str, max_results: int = 5) -> WebResult`、`close() -> None`。
`SearchService`はアプリ単位で共有する。DDGS呼出しはdependency注入できるようにし、テストでは実通信しない。

- [ ] **RED:** `test_ddgs_receives_fixed_backend_and_region`で次をassertする。
  ```python
  assert kwargs == dict(backend="duckduckgo", region="jp-jp",
                       safesearch="moderate", max_results=5)
  ```
  `test_validates_query_and_count`で空白のみ、501文字、件数0/11を拒否する。
- [ ] **RED:** `test_normalizes_and_filters_results`で正規化URLの重複除去、内部DNS/非HTTP結果除外、title300/snippet1000文字をassert。`test_empty_and_failed_search_are_distinct`で空listはno_results、曖昧なDDGS例外はsearch_failedとする。
- [ ] **RED:** `test_timeout_does_not_release_running_worker_slot`をthreading.Event制御で追加。2実workerを止めて複数回timeout/cancelしても実worker最大2、待ち要求は30秒内に終了し、解放後に新規検索が成功する。短い注入deadlineを使い実時間30秒待たない。
- [ ] **RED確認:** `uv run --frozen pytest tests/test_web_search.py -q`の失敗を確認。
- [ ] **GREEN実装:** `uv add ddgs`。バックエンドの存在を確認してから固定名で呼び、未対応時は失敗にする（DDGSのauto fallbackを許さない）。`DDGS(timeout=10, verify=True)`をmax_workers=2のexecutorで実行。キュー待ち・結果URLのDNS検証も含め30秒、実futureの完了までslotを返さず、executorへ無制限にsubmitしない。検索結果本文はここでは取得しない。closeは待機中futureをcancelし、動作中workerの終了を記録・管理する。HTTP 429等で確実な場合だけrate_limitedへ分類。
- [ ] **GREEN確認:** `uv run --frozen pytest tests/test_web_search.py tests/test_web_http.py -q`が全PASS。
- [ ] **レビュー地点:** timeout/cancel後の実futureとslotの寿命、採用DDGS版のPython対応・ネイティブ依存を確認。
- [ ] **Commit:** Task 3のファイルと依存2ファイルをstageし、`git commit -m 'feat(web): add keyless bounded DuckDuckGo search'`。

### Task 4: 内蔵ツール専用のレジストリアダプター

**Files:** Create `src/use_lllm/web/registry.py`, `tests/test_web_registry.py`; Modify `web/contracts.py`。

**Interfaces:** `WebRegistry(delegate: MCPRegistry, *, search: SearchService, pages: PageReader, enabled: bool)`。
`search`/`pages`はTask 2/3の型を使い、2ツールのschemaを`ToolDescription`と`RegisteredTool`で保持する。
delegateの公開メソッド契約を維持し、変更点は`tools`, `tool_names`, `ollama_tools`, `get_tool`,
`server_instructions`, `decide`, `call_tool`, `is_replay_safe`, `may_write_files`,
`connection_generation`, `ensure_session`, `close_all`に集約する。
他の`server_names/spec/status/statuses/connect/disconnect/connect_autostart/resources/prompts/read_resource/get_prompt/close_session`はdelegateへ委譲する。

- [ ] **RED:** `test_enabled_exposes_exactly_two_tools_and_auto_allows`でannotations=readOnly/openWorld、decision.allowed=True、approval_required=False、MCP引数検証と結果形式をassert。
- [ ] **RED:** `test_disabled_hides_and_rejects_direct_calls`, `test_respects_excluded_tools_and_unambiguous_alias`, `test_collision_rejected_even_when_disconnected`を追加。外部webサーバーの有無は接続状態でなく登録specで判定する。
- [ ] **RED:** `test_external_annotations_do_not_gain_auto_approval`、`test_delegate_resource_and_lifecycle_contracts`を追加する。Web有効化後もms-data-parserの外部通信は従来どおり承認待ち。
- [ ] **RED確認:** `uv run --frozen pytest tests/test_web_registry.py -q`で期待する欠如による失敗を確認。
- [ ] **GREEN実装:** 内部で保持する2ツールオブジェクトと実装dispatchのみを信頼し、外部schemaを昇格させない。aliasは内蔵・delegate両方で一意の場合のみ解決する。`ollama_tools`はexcludedを尊重し、通常のtool catalogueの順位・limit契約を維持する。内蔵接続世代は1、状態復旧前提なし、外部ファイル書込なし。`close_all`はdelegateを閉じるが、アプリ共有のWebサービスの寿命はTask 5のruntimeが管理する。無効時はサービス起動・通信なし。Web無効時に外部MCP名webが存在する場合は通常MCPとして委譲し、自動許可しない。
- [ ] **GREEN確認:** `uv run --frozen pytest tests/test_web_registry.py tests/test_mcp_registry.py tests/test_policy_server.py tests/test_tool_catalog.py -q`が全PASS。
- [ ] **レビュー地点:** 自動許可が名前文字列や外部annotationsだけから生まれていないことを確認。
- [ ] **Commit:** Task 4のファイルをstageし、`git commit -m 'feat(web): expose trusted built-in tools through registry'`。

### Task 5: 設定永続化とAPI統合

**Files:** Modify `src/use_lllm/core/settings_store.py`, `src/use_lllm/general/api.py`, `tests/test_settings_store.py`, `tests/test_general_api.py`; Create `tests/test_web_api.py`。

**Interfaces:** `Settings.web_search_enabled: bool = False`。`WebSearchBody`は`enabled: StrictBool`のみ（extra禁止）。
`PUT /general/api/web-search`は更新後の`runtime.public_settings()`を返す。
`GET /general/api/settings`に`web_search_enabled`を追加。JSON保存キーも同名。

- [ ] **RED:** `test_legacy_web_setting_defaults_off`, `test_web_setting_round_trip_and_strict_bool`。旧JSONがoff、true保存後再読込true、文字列falseなどは設定エラー。
- [ ] **RED:** `test_updates_web_settings_with_csrf`, `test_rejects_extra_fields_and_name_collision`で正常200、CSRFなし403、型/extra不正422、名前衝突409をassert。
- [ ] **RED:** `test_busy_rejects_web_toggle_without_partial_save`。`runtime._active_turns`が空でなければ409、settings/registry/file全て変更なし。
- [ ] **RED:** `test_all_connection_mutations_preserve_web_setting`。AI追加・編集・削除・選択、`select_model`、MCP追加・編集・削除・Claude取込をparameterizeし、trueを保持する。Web有効中にMCPをwebへ改名・追加・取込すると409で変更なし。`test_reconfigure_preserves_shared_search_workers`でtimeout後のオン/オフ・モデル切替でもSearchServiceを作り直さず、実worker上限2を維持する。
- [ ] **RED確認:** `uv run --frozen pytest tests/test_settings_store.py tests/test_web_api.py -q`。
- [ ] **GREEN実装:** 設定変更は`dataclasses.replace`で既存Settingsを引き継ぐ。`GeneralRuntime._install`で既存factoryのsignatureを変えず、作ったregistryをWebRegistryで包む。衝突検証→新registry構築→保存・適用の順とし、失敗時は旧registryを維持する。SearchService/PageReaderはruntimeが初回有効化時に遅延構築し、再設定では再利用する。アプリlifespan終了時のみサービスをcloseする。無効時は保持したサービスへ新要求を通さない。通常MCPのstatusesは既存形式を維持し、Web状態は独立フィールドで示す。
- [ ] **GREEN確認:** `uv run --frozen pytest tests/test_settings_store.py tests/test_web_api.py tests/test_general_api.py tests/test_azure_openai.py -q`が全PASS。
- [ ] **レビュー地点:** `rg -n 'Settings\(' src`で全再構築経路を確認し、保存済みWebフラグ消失経路を残さない。
- [ ] **Commit:** Task 5のファイルをstageし、`git commit -m 'feat(web): persist opt-in search settings and API'`。

### Task 6: 自発的検索instructionsと発話単位の上限

**Files:** Create `src/use_lllm/web/{instructions,budget}.py`, `tests/test_web_agent.py`; Modify `src/use_lllm/general/agent_loop.py`, `tests/test_general_agent.py`。

**Interfaces:** `WEB_INSTRUCTIONS: str`。`reset_web_budget(sessions: SessionStore, session_id: str) -> None`、
`consume_web_budget(sessions: SessionStore, session_id: str, tool_name: str) -> WebResult | None`。
stateキーは`web_budget: {search: int, read_page: int}`。成功・失敗とも実行試行を計数する。
超過時は`rate_limited`と`limit_scope="turn"`を持つWebResultを返し、外部サービスには呼び出さない。

- [ ] **RED:** `test_web_instructions_follow_tool_availability`。両方無効ならinstructionsなし、片方だけ利用可能なら存在するツールだけを案内する。通常のサーバinstructionsとの重複を避ける。
- [ ] **RED:** `test_fact_check_flow_for_both_providers`をOllama型/Azure型×stream/nonstreamでparameterize。偽モデルがsearch→read_page→Markdown出典回答を返し、呼出順・入力結果の受渡し・履歴・SSE・承認待ちなしをassert。
- [ ] **RED:** `test_budget_search_four_page_six`。5回目search/7回目read_pageでサービス呼出しなし・構造化エラー、他ツールは影響なし。`test_budget_survives_approval_resume_and_agent_rebuild`で既存MCP承認前後の累積を検証し、新ユーザー発話でのみ0へ戻る。`test_disabled_web_tool_cannot_be_called_by_hallucinated_alias`で非公開ツールをモデルが直接指定してもサービスへ到達しない。
- [ ] **RED:** `test_translation_and_explicit_no_search`はWeb可でもツールなし回答を通し、渡したinstructionsに検索不要条件・ユーザー制約優先があることをassert。`test_failed_search_is_presented_as_unverified`は失敗結果を偽モデルが受け取り、未確認の回答が記録されることを確認。
- [ ] **RED確認:** `uv run --frozen pytest tests/test_web_agent.py -q`。
- [ ] **GREEN実装:** spec §5の方針をinstructionsへ記述する。`_begin_user_message`でbudget初期化し、全ツール呼出し共通の`_registry_call`で内蔵ツールの正規名解決後にsessionのdisabled_toolsを検査してから計数する。拒否・超過は既存履歴へ構造化エラーとして渡す。stream/通常/承認再開のそれぞれで元の結果処理へ統一して渡す。無効化後の古い承認からWeb権限は復活しない。20ステップ上限は変更しない。
- [ ] **GREEN確認:** `uv run --frozen pytest tests/test_web_agent.py tests/test_general_agent.py tests/test_state_recovery.py tests/test_context_memory.py -q`が全PASS。
- [ ] **レビュー地点:** 偽モデルのテストは実行経路の検証であり、自発的検索やファクトチェック品質の実証ではないことを記録する。
- [ ] **Commit:** Task 6のファイルをstageし、`git commit -m 'feat(agent): guide factual web checks with per-turn limits'`。

### Task 7: 見つけやすいUIと全体検証

**Files:** Modify `src/use_lllm/general/static/{index.html,app.js,i18n.js,styles.css}`, `tests/test_general_ui.py`, `tests/general_ui_fixture.py`, `README.md`; Create `tests/test_web_integration.py`, `tools/web_smoke.py`, `docs/superpowers/verification/2026-10-15-web-fact-checking.md`（検証結果・未確認範囲）。

**Interfaces:** DOM IDs `web-search-section`, `web-search-enabled`, `web-search-status`, `web-search-error`。
`renderWebSearchSettings()`と`saveWebSearchSettings(event)`をapp.jsに追加し、既存`api()`経由でPUTする。
`tools/web_smoke.py`はCLIで`--query`/`--url`を受け、公開情報の実検索・取得結果をJSONへ出す。
`--model-eval`モードは追加しない（実モデル評価は隔離したUIで実施する）。

- [ ] **RED:** `test_web_ui_contract_and_bilingual_copy`（上部の独立section、APIキー不要・通信先・個別確認なし説明、日英辞書）、`test_web_toggle_error_does_not_show_success`（保存失敗で元状態へ復帰）を追加。
- [ ] **RED:** `tests/test_web_integration.py`で設定オン→fake検索→fake本文→出典回答→オフのAPIシナリオを追加。既存MCPの承認が維持され、履歴が安全に描画されることをassertする。
- [ ] **RED確認:** `uv run --frozen pytest tests/test_web_integration.py tests/test_general_ui.py -q`。
- [ ] **GREEN実装:** Settingsパネルの言語欄直後にWeb欄を置く。更新中はswitch無効化、成功後だけstate更新、失敗は欄内表示。Connected toolsと既存disabled_toolsを再利用する。進行表示は`Web検索中`/`ページを確認中`とし、エラー結果に`完了`だけを表示しない。ユーザーデータをinnerHTMLへ渡さない。READMEに使い方・制限・実モデル評価の区別を記載する。
- [ ] **GREEN確認:** 上記と`uv run --frozen ruff check .`、`uv run --frozen pytest -q`、`uv lock --check`、`node --check src/use_lllm/general/static/app.js`、`node --check src/use_lllm/general/static/i18n.js`が成功する。
- [ ] **UI確認:** `uv run --frozen python -m tests.general_ui_fixture 50840`で合成データ専用サーバーを起動（port競合なら空きportを使用）。ブラウザーで日英表示、オン/オフの保存・再読込、エラー、検索中/取得中、出典リンク、通常MCP承認を確認し、スクリーンショットを保存。実アプリのport/保存先を使わず、確認後fixtureを停止する。
- [ ] **実通信smoke:** `uv run --frozen python tools/web_smoke.py --query 'Python documentation pathlib' --url 'https://docs.python.org/3/library/pathlib.html'`。backend/タイトル/URL/取得日時/抽出本文を確認。ネット制限で失敗した場合はコードの単体テスト合格と区別し、実通信未確認と記録する。
- [ ] **実モデル評価:** 利用可能なOllamaと設定済みAzure各1モデルを、チャット履歴のない隔離保存先で評価する。公開情報のみで「Pythonの最新安定版と公式根拠」「架空の更新情報の事実確認」「短文翻訳」「検索せず提示資料だけで回答」を試す。実行時にモデル・接続先・課金呼出しの範囲を明示し、未承認の外部データ送信を含めない。各ケースのtool呼出し、取得URL、引用の対応、失敗時の未確認表示を記録する。未実施のプロバイダーは未確認と報告する。
- [ ] **レビュー地点:** 全体diffをspecの受入条件と照合。認証情報、実設定、実データ、外部MCPの権限変更が含まれないことを確認し、実行方法に応じたコードレビューを行う。
- [ ] **Commit:** Task 7のファイルだけをstageし、`git commit -m 'feat(ui): add opt-in automatic web fact checking'`。`git status --short`と`git diff --check`がclean。実アプリへの適用・再起動、merge/pushは実施内容をユーザーに報告して進める。

## 受入条件との対応

| Spec §8 | 主なTask | 必須の証拠 |
|---|---|---|
| 1 設定・無効時の通信なし | 4, 5, 7 | 旧設定/保存/直接呼出し拒否/接続切替のテスト |
| 2 内蔵限定の自動実行 | 4, 5 | 名前衝突、外部annotations、通常MCP承認のテスト |
| 3 検索結果・失敗区別 | 3 | fake DDGSと実検索smokeを分けて記録 |
| 4 本文・日時・URL | 2 | 抽出fixture、truncated、日時、redirectテスト |
| 5 通信・時間・回数制限 | 1, 2, 3, 6 | connect/SNI観測、robots、容量、worker、budgetテスト |
| 6 stream/通常/再開の一貫性 | 6 | 同じシナリオを全経路で検証 |
| 7 両LLMの流れ | 6, 7 | fake clientと実モデル評価を明確に分離 |
| 8 UI・既存機能維持 | 7 | 合成fixtureの画面確認と既存UI/API回帰 |
| 9 決定的テストと実通信の分離 | 1〜7 | 自動テストの外部通信0件、smoke結果 |
| 10 自発的検索と引用品質 | 7 | モデル/接続先/クエリ/引用の評価記録または未確認表示 |

## 実行引き継ぎ

計画レビュー後に実行方法を選択する。

- **本人が順番に実装（推奨）:** executing-plansで7Taskを進める。共有する結果型・レジストリ契約の連続性を保ちやすい。最後に独立した全体レビューを行う。
- **サブエージェントで実装:** subagent-driven-developmentでTaskごとの実装・レビューを行う。各Taskの独立レビューを重視する場合に選ぶ。

推奨理由: 今回はネットワーク取得、結果型、レジストリ、設定、agentが順に依存し、並列化の利益より契約を一貫して追う利点が大きい。
この文書の保存・レビューは実装開始や本番設定の変更を意味しない。
