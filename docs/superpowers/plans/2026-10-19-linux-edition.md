# Use-LLLM Linux Edition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ubuntu の ~/Use-LLLM-Linux へ専用仮想環境付きで導入し、研究室・共同研究者がアプリ一覧から Ollama／Azure と MCP を利用できる配布版を作る。

**Architecture:** Python／WebUI 本体を Windows と共用し、配置、秘密保管、Linux 起動管理を小さなモジュールへ分離する。~/Use-LLLM-Linux/releases/<release-id> に各版のソースと .venv を配置し、current を原子的に切り替える。設定と会話は XDG のユーザー領域へ分離する。

**Tech Stack:** Python 3.12.13、uv と uv.lock、FastAPI／Uvicorn、SQLite、Secret Service／keyring、Bash、freedesktop .desktop、pytest／Node／Playwright。

**Spec:** [承認済み設計書](../specs/2026-10-19-linux-edition-design.md)

## Global Constraints

- Ubuntu 24.04 LTS・x86_64・GNOME デスクトップを受入検証基準とする。
- ~/ は対象 Linux ユーザーのホーム。Windows の C:/Users/yuu18 に Linux 用インストールを作らない。
- アプリ配置ルートは ~/Use-LLLM-Linux。独立したコードの fork は作らず、共通ソースから配布物を生成する。
- 初回導入・更新時はオンライン、通常起動では依存取得・自動更新をしない。
- root 実行と sudo pip を使わない。uv がなければ導入手順を表示し、非ゼロで終了する。
- 初版は現在の開発環境と同じ Python 3.12.13 を固定。実装開始時に配布可否を確認し、変更する場合は manifest と検証記録を同時更新する。
- 実行用の依存は uv.lock で固定。仮想環境は最終リリースパスで作り、移動・コピーで配布しない。
- API キーの平文保存は禁止。Windows DPAPI と既存の保存先・API 契約を維持する。
- DB スキーマ変更、LAN 公開、ARM、外部 MCP の移植、Ollama／モデル同梱を含めない。
- 製品の既存セキュリティと、利用者の会話・設定を保持する。
- 選択的コミットを行い、既存の未追跡 .codex/ を追加しない。

## Review Focus

1. ホームパスに空白・日本語・% がある: CLI と .desktop の引数が壊れず、展開をシェルに再解釈させない（Task 5）。
2. Azure 選択中に keyring がロック: 起動と Ollama への切替が可能で、保存済み参照を消さない（Task 3）。
3. PID 再利用・偽の health・同時起動: 無関係なプロセスを再利用・停止しない（Task 4）。
4. 更新途中のダウンロード失敗・ディスク不足: current、旧 .venv、利用者の設定を壊さない（Task 6）。
5. 任意パス・シンボリックリンクを含むアーカイブや削除対象: 管理領域外へ書かず、削除しない（Task 6）。

## 現状の根拠と検証環境

- core/sessions.py: default_data_directory() は LOCALAPPDATA／AppData/Local を前提とする。
- core/secret_protection.py: _windows_cryptography() は非 Windows で ConfigurationError。
- core/settings_store.py: load_settings() 時の秘密復号、save_settings() 時の暗号化が設定全体に結合している。
- core/endpoints.py: EndpointRegistry は全 Endpoint を validate するため、Azure キー欠落を単に None にするだけでは起動不能になる。
- general/api.py: GeneralRuntime._install() は選択済み接続のクライアントを起動時に作る。配置分離と利用不能接続の扱いをここへ接続する。
- tests/test_general_browser.cjs は .venv/Scripts/python.exe と msedge を既定値に持つ。
- 2026-10-19 の wsl --list --quiet は docker-desktop のみ。通常の Ubuntu ディストリビューションと GNOME 環境は未確認。
- Docker CLI は存在するが daemon の稼働は未確認。環境の新規導入や既存ディストリビューション変更は計画作成では行わない。
- 実装時は利用可能な Linux 環境から検証し、Ubuntu GNOME 実機／VM、実 Azure、実 Ollama の未実施項目を残す。WSL／コンテナだけで配布受入完了としない。

## ファイルと責務

| ファイル | 責務 |
|---|---|
| src/use_lllm/core/platform_paths.py（新規） | AppPaths と XDG／Windows 保存先計算 |
| src/use_lllm/core/secret_store.py（新規） | SecretStore と DPAPI／Secret Service の実装 |
| core/secret_protection.py（既存） | DPAPI 処理をそのまま再利用 |
| core/endpoints.py、core/settings_store.py、general/api.py | 秘密参照・利用可否・設定の原子的保存 |
| general/static/app.js、i18n.js | 接続利用不能の理由と復旧操作を日英で表示 |
| core/sessions.py、general/api.py | 分離した保存先を利用 |
| src/use_lllm/linux/instance.py（新規） | ロック、プロセス識別、管理情報 |
| src/use_lllm/linux/launcher.py（新規） | start／stop／status、ヘルス待機、ログ、通知 |
| src/use_lllm/cli.py、general/api.py | Linux 子プロセス起動と追加 health 識別情報 |
| tools/linux/release.py（新規） | 許可リストによるリリース生成・manifest・検証 |
| tools/linux/deploy.py（新規） | 導入、更新、バックアップ、削除（標準ライブラリのみ） |
| Setup-Linux.sh、Start-WebUI.sh、Update-Linux.sh、Uninstall-Linux.sh（新規） | uv と絶対パスを使う薄い入口 |
| docs/linux/README.md（新規）、README.md | 導入・運用・制限 |
| tests/test_linux_*.py、test_platform_paths.py、test_secret_store.py（新規） | 上記の振舞いと失敗時の保持を検証 |

後続タスクの修正パスは src/use_lllm/ 配下を基準とする。関係しない大規模リファクタリングはしない。

### Task 1: 保存先とユーザー権限を分離する（3～5時間）

**Files:** Create core/platform_paths.py; modify core/sessions.py, general/api.py; test tests/test_platform_paths.py, tests/test_setup_store.py。

**Interfaces:** `AppPaths(app_root: Path, config_dir: Path, data_dir: Path, state_dir: Path, runtime_dir: Path)` と `resolve_paths(*, platform: str | None = None, home: Path | None = None, environ: Mapping[str, str] | None = None) -> AppPaths`。`ensure_private_directory(path: Path) -> None`。既存 default_data_directory() は委譲して署名を維持する。

- [ ] RED: `test_linux_paths_follow_xdg` で app_root == home/'Use-LLLM-Linux'、設定 == XDG_CONFIG_HOME/'use-lllm'、data == XDG_DATA_HOME/'use-lllm/data' を assert。`test_data_override_keeps_settings_together`、`test_windows_paths_unchanged`、相対 XDG 値の無視、runtime 未設定の専用状態領域への退避を追加。
- [ ] 実行: `uv run --frozen python -m pytest tests/test_platform_paths.py -q`。新規モジュール未実装により失敗することを確認。
- [ ] GREEN: 上記インターフェースを実装。general/api.py で明示的な session_store／settings_path 注入を優先し、既存 fixture の分離を保持。setup.json は config_dir、DB・成果物は data_dir。Linux で 0700／0600 を適用し、Windows で POSIX 権限検査を強制しない。
- [ ] 検証: 新規テストに加え `tests/test_sessions.py tests/test_setup_store.py tests/test_app_standalone.py tests/test_general_api.py`。Linux で実ファイルの mode を確認。
- [ ] 対象ファイルのみ commit: `feat(platform): separate Linux user storage paths`。

### Task 2: OS ごとの秘密保管と参照を定義する（4～6時間）

**Files:** Create core/secret_store.py, tests/test_secret_store.py; modify core/endpoints.py, pyproject.toml, uv.lock。

**Interfaces:** `SecretStore.read(reference: str) -> str`、`write(value: str) -> str`、`delete(reference: str) -> None`。`SecretUnavailable(ConfigurationError)` と `get_secret_store() -> SecretStore`。Linux 参照形式 `secret-service-v1:<uuid>`、service 名 `Use-LLLM`。Endpoint に末尾既定値フィールド `credential_id: str | None = None`、`secret_reference: str | None = None`、`credential_error: str | None = None` を追加。api_key はメモリー上のみ、既存 Windows のシリアライズ形式は維持。

- [ ] RED: `test_secret_service_roundtrip`、`test_no_fallback_backend`、`test_unavailable_is_typed_error`、`test_locked_collection_does_not_prompt_indefinitely`、`test_windows_dpapi_roundtrip_unchanged`。fake backend で秘密値を既知文字列として保存・復元し、利用不能時にファイルが増えないことを assert。
- [ ] 実行: `uv run --frozen python -m pytest tests/test_secret_store.py -q`、未実装による失敗を確認。
- [ ] GREEN: DPAPI アダプターは既存 protect_secret／unprotect_secret を呼ぶ。Linux は keyring.backends.SecretService.Keyring を直接選択し、チェーン型や平文 backend を自動選択しない。ロック中は自動 unlock を要求せず SecretUnavailable とし、解除案内を返す。keyring／SecretStorage は Linux 環境マーカー付き依存に追加し uv.lock を再生成する。
- [ ] 検証: fake の OS 共通テストと、Windows 実 DPAPI、Linux 実 Secret Service の結果を分ける。Python の公開最低バージョン >=3.11 は上げない。
- [ ] commit: `feat(secrets): add explicit Linux Secret Service backend`。

### Task 3: 設定保存と Azure 利用不能状態を統合する（6～10時間）

**Files:** Modify core/settings_store.py, core/endpoints.py, general/api.py, general/static/app.js, general/static/i18n.js; test tests/test_settings_store.py, tests/test_endpoints.py, tests/test_general_api.py, tests/test_linux_credentials.py, tests/test_general_i18n.cjs。

**Interfaces:** `load_settings(path: Path, *, secret_store: SecretStore | None = None) -> Settings` と `save_settings(path: Path, settings: Settings, *, secret_store: SecretStore | None = None) -> None`。`Endpoint.available` プロパティ、`Endpoint.validate(*, require_credentials: bool = True) -> None`。構造検証と資格情報可用性を分け、Azure への実アクセス時は必ず require_credentials=True。settings API に `available: bool`、`credential_error: str | None` を追加し、secret_reference 自体をブラウザーへ返さない。

- [ ] RED: `test_locked_selected_azure_still_serves_settings`、`test_switch_to_ollama_preserves_locked_reference`、`test_failed_replace_keeps_old_secret_and_settings`、`test_rename_keeps_credential_id`、`test_delete_removes_secret`、`test_imported_dpapi_does_not_crash_linux`。POST／PATCH／削除など現行の実 API 経路で既存セキュリティトークンも使用。
- [ ] 実行: 上記 pytest 対象で失敗を確認。秘密を含む入力がレスポンス／ログ／JSON に出ないことも assert。
- [ ] GREEN: 利用不能 Azure のクライアントは health で利用不能を返し、チャットでは明確な設定エラーを返す。選択状態を自動変更せず、画面から Ollama へ切替可能にする。元参照は保持し、別接続設定の保存で欠落させない。保存の順序は新規秘密→一時 JSON→os.replace→古い秘密削除。replace 失敗時は新規秘密だけ回収。削除失敗は安全な再試行用情報を残し、秘密をログへ出さない。
- [ ] 検証: 既存 settings／endpoint／Azure／API の回帰と JA／EN 表示。Windows の旧平文キー自動移行と DPAPI 形式を保持。Linux で未知の保護形式は明示エラー、DPAPI は再入力対象として保持する。
- [ ] commit: `feat(settings): tolerate unavailable Linux credentials without data loss`。

### Task 4: Linux 起動と所有プロセスの管理（6～9時間）

**Files:** Create linux/__init__.py, linux/instance.py, linux/launcher.py, tests/test_linux_launcher.py; modify cli.py, general/api.py。

**Interfaces:** `InstanceRecord(pid: int, uid: int, start_ticks: int, port: int, instance_id: str, release_id: str)`。`start(*, open_browser: bool = True) -> int`、`stop() -> int`、`status() -> dict[str, object]`。`python -m use_lllm.linux.launcher {start,stop,status}`。exit 0 成功、1 不在／起動失敗、2 不正引数／設定、3 他プロセス所有／競合。Linux 用 health に instance_id／release_id を追加し、既存フィールドは維持。

- [ ] RED: `test_double_start_reuses_owned_server`、`test_foreign_health_not_reused`、`test_reused_pid_never_signalled`、`test_concurrent_start_has_one_server`、`test_busy_default_port_uses_new_port`、`test_oauth_callback_port_matches`、`test_timeout_keeps_diagnostic_log`、`test_browser_failure_keeps_server`。clock と subprocess を注入し、30秒待つテストは実時間で走らせない。
- [ ] 実行: `uv run --frozen python -m pytest tests/test_linux_launcher.py -q`。Linux 専用 flock／proc の実テストは Windows で理由を明記して skip。
- [ ] GREEN: Linux fcntl ロック、/proc の UID・開始 ticks・実行対象・health を照合。ready は0.2秒間隔で最大30秒、bind競合の再試行は最大3回。子は --open を付けず serve し、USE_LLLM_WEB_PORT／INSTANCE_ID／RELEASE_ID を渡す。待機成功後に webbrowser で URL を開く。
- [ ] GREEN: ログは所有者専用、5 MiB・3世代。stop は管理対象へ SIGTERM、最大10秒待機後も残る場合はエラーを返す（無関係な PID やプロセスグループへ送信しない）。通知は利用可能な notify-send に引数配列で渡し、失敗時もログと stderr を残す。
- [ ] 検証: Linux 実子プロセスで start／status／stop と MCP の正常終了を確認。Windows の cli／launcher-health テストも実行。
- [ ] commit: `feat(linux): add verified desktop server lifecycle`。

### Task 5: 配布生成・初回セットアップ・ショートカット（5～8時間）

**Files:** Create tools/linux/release.py, tools/linux/deploy.py, Setup-Linux.sh, Start-WebUI.sh, Update-Linux.sh, Uninstall-Linux.sh, tests/test_linux_release.py, tests/test_linux_install.py; modify .gitattributes。

**Interfaces:** `build_release(destination: Path, release_id: str) -> Path`。`install_release(archive: Path, *, home: Path, uv_executable: Path) -> Path`。標準ライブラリの deploy.py CLI は `install ARCHIVE`／`update ARCHIVE`／`uninstall [--purge-data]`。manifest.json に schema_version=1、release_id、python_version=3.12.13、全ファイルの SHA256 を記録。release_id は `[A-Za-z0-9][A-Za-z0-9._-]{0,79}`、ドット単独と .. は拒否。

- [ ] RED: `test_archive_contains_assets_not_private_files`、`test_setup_creates_venv_at_final_path`、`test_desktop_exec_quotes_special_home`、`test_missing_uv_reports_next_action`、`test_setup_failure_has_no_current`、`test_source_and_windows_install_unchanged`。home を `研究室 user%` とし Exec の引用と % エスケープ、shell 注入不能を assert。
- [ ] 実行: `uv run --frozen python -m pytest tests/test_linux_release.py tests/test_linux_install.py -q`。
- [ ] GREEN: 許可リストで src（vendor/fonts 含む）、pyproject、lock、Linux 入口・導入コード・文書を梱包する。tools は Python 起動に必要な同梱コードを絶対パスで参照。全 .sh を LF／0755 へ固定。外側の SHA256SUMS も生成する。生成物は dist/ 配下。
- [ ] GREEN: Setup-Linux.sh は既存 uv の絶対パスを検出し、`uv run --no-project --python 3.12.13 python <deploy.py> install <archive>` で bootstrap。deploy は最終 releases/<id> で `uv sync --frozen --no-dev --python 3.12.13`。一時データの確認が成功してから current と安定起動入口を作成する。
- [ ] GREEN: ルート ~/Use-LLLM-Linux/ の各 .sh は current を介して動く安定入口。~/.local/bin/use-lllm-webui と .desktop は同じ入口を参照し、端末の PATH／cwd に依存しない。アプリ一覧へ登録するだけでデスクトップへの自動コピーはしない。
- [ ] 検証: package-data の欠落を wheel 内容でも確認。fake uv のテストに加え、Linux の一時 home でオンライン初回導入し、完成 .venv からヘルス応答を取得。
- [ ] commit: `feat(linux): package and install isolated home-directory release`。

### Task 6: 更新・復旧・削除（5～8時間）

**Files:** Modify tools/linux/deploy.py; create tests/test_linux_update.py, tests/test_linux_uninstall.py。

**Interfaces:** `update_release(archive: Path, *, home: Path, uv_executable: Path) -> Path`、`uninstall(*, home: Path, purge_data: bool = False) -> None`。バックアップは state_dir/backups/<timestamp>、古いリリースは保持。タスク5の install_release と同じ検証・配置処理を共用する。

- [ ] RED: `test_running_update_refuses_without_stopping`、`test_download_or_enospc_preserves_current`、`test_bad_archive_does_not_escape_root`、`test_symlink_root_is_not_recursively_removed`、`test_update_backs_up_sqlite_consistently`、`test_default_uninstall_preserves_data_and_secrets`、`test_purge_only_removes_app_owned_entries`。
- [ ] 実行: `uv run --frozen python -m pytest tests/test_linux_update.py tests/test_linux_uninstall.py -q`。
- [ ] GREEN: 起動／更新が同時に走らない管理ロックを共用。アーカイブは通常ファイル・ディレクトリのみ、絶対／..／ハードリンク／symlink／device を拒否。所有者と解決後の絶対パスを検査し、既存任意フォルダーを上書きしない。同一 release_id の重複導入は既存を変更せず拒否。
- [ ] GREEN: 停止済みを確認し、SQLite backup API と設定コピーで退避してから新版環境を作成。テスト用データでのヘルス成功後に current を原子的切替。切替後の起動失敗時は current を旧版へ戻し、新版を未完了として記録。初版では設定形式変更も後方互換を保ち、Windows 版を含む旧版が読めない設定へ自動移行しない。
- [ ] GREEN: 既定削除は管理する app_root／入口／.desktop のみ。--purge-data のみデータ・設定とアプリ所有の秘密参照を削除。keyring ロック中の purge は未削除を報告し、成功扱いにしない。現在動作中のアプリ、共有キャッシュ、外部ツールは削除しない。
- [ ] 検証: Linux の一時 home で A→失敗B→正常B→既定削除を行い、DB の既知履歴と設定・キー参照が保持されることを確認。
- [ ] commit: `feat(linux): add recoverable updates and scoped uninstall`。

### Task 7: 統合試験・導入文書・配布確認（6～10時間）

**Files:** Modify tests/test_general_browser.cjs, README.md; create docs/linux/README.md, tests/test_linux_acceptance.py, docs/superpowers/verification/2026-10-19-linux-edition.md。

**Interfaces:** ブラウザー試験は PYTHON と PLAYWRIGHT_BROWSER_CHANNEL を受け、Linux は Chromium、Windows は既存 msedge を既定とする。実外部サービスの試験は明示 opt-in とし、Azure キーを引数・記録・成果物へ含めない。

- [ ] RED: `test_browser_fixture_python_is_platform_aware` と Linux 配布後 fixture で起動・セッション保存・HTML 書き出し。stdio／HTTP テスト MCP と許可・拒否・終了後再接続を検証。
- [ ] GREEN: ブラウザー試験を OS に対応し、docs/linux/README.md に展開→uv 準備→Setup-Linux.sh→アプリ一覧→status／stop→更新→削除を実行可能な順序で記載。Secret Service の依存／解除、Ollama 別途導入、Azure キー再入力、通信先・プロキシも記載。
- [ ] 自動検証: Linux と Windows で `uv run --frozen python -m pytest -q`、`uv run --frozen ruff check src tests tools`。`tests/test_general_*.cjs` をファイル列挙して実行し、ブラウザー試験のみ必要なランタイムを指定。全 .sh に `bash -n`、desktop-file-validate が利用可能なら .desktop を検査。
- [ ] Ubuntu GNOME 受入: 新規ユーザーでオンラインセットアップ、アプリ一覧からの実起動、通常起動時の依存取得なし、二重起動、ポート競合、キー保管の正常／ロック状態、Ollama 実会話、Azure 保存後の再起動・実会話、MCP、JA／EN、画像・プロット・HTML を確認。
- [ ] Windows 回帰: 現在の Start-WebUI.ps1 と DPAPI 設定を検証し、既存利用者の会話／キーを試験目的で変更しない。実試験は専用データ領域を指定する。
- [ ] 記録: commit、OS／Python／依存版、実行コマンド、結果、アーカイブ SHA256 と未実施理由を記載。実機なしは「自動検証済み・デスクトップ受入待ち」とする。完了扱いを分ける。
- [ ] commit: `test(linux): verify desktop distribution and document operations`。

## 工数と進行判断

| 作業 | 人時 |
|---|---:|
| Task 1 配置 | 3～5 |
| Task 2 秘密保管 | 4～6 |
| Task 3 設定統合 | 6～10 |
| Task 4 起動管理 | 6～9 |
| Task 5 配布・導入 | 5～8 |
| Task 6 更新・削除 | 5～8 |
| Task 7 統合・文書 | 6～10 |
| 合計 | **35～56時間（約4.5～7人日）** |

設計で具体化した更新復旧・キー保管不能時の継続利用・プロセス識別を含む再見積もり。
初期3～5人日より増える。人による作業量の目安であり、AI の実経過時間・完了時刻の保証ではない。
新規 VM／Ubuntu デスクトップの構築、外部サービス待ち、MCP サーバー側改修は含めない。

依存順序: Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7。
実装はこのチャットで主担当が順に進める方式を推奨する。秘密参照、設定保存、起動・更新の共有インターフェースが密接なため。
承認後は executing-plans と using-git-worktrees に従い、適切なチェックアウトを確認して着手する。
サブエージェント方式を希望する場合は、同じ計画でタスクごとに実装・レビューを委譲する。

## 計画の自己レビュー

- 配置変更は設計書へ反映済み。~/Use-LLLM-Linux はアプリで、会話・設定は分離。
- 設計書1～5節: Task 1～3／5。6節: Task 4。7節: Task 5～6。8節: Task 3～4／6。9節: Task 7。
- Review Focus の5項目に対応する失敗テストを各タスクに割当済み。
- API の既存位置引数を維持し、新引数は keyword-only。設定保管と秘密削除は一つの保存処理内で管理。
- 実装・依存導入・Ubuntu 環境の作成は本計画作成では実施していない。
