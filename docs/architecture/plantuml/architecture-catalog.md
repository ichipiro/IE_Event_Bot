# PlantUML アーキテクチャ・カタログ

このカタログは、現行チェックアウトの実装・設定・文書を静的に調査し、PlantUML 図と `architecture-index.json` の共通根拠を示す。Cloudflare、Google、Notion、Discord の実環境設定、権限、疎通、実行成功は確認していない。

- 根拠リビジョン: `63874dabdcdeae05f5e18e82e3598a9f2c0b753a`（未コミットの現行ソースを含む）
- 記号探索対象: `workers/src/*.py`、`tools/validate_plantuml.py`、`tools/utf8-no-bom.ps1`、`typings/workers/__init__.pyi`
- 設定・文書根拠: `workers/wrangler.jsonc`、`pyproject.toml`、`.github/workflows/*.yml`、`README.md`、`docs/*.md`
- 対象外: `.git/`、`.venv/`、`__pycache__/`、生成済み `artifacts/`。機密ファイル名に該当する `workers/service-account.json`、`.dev.vars*` は読み取っていない。

## 網羅性

| 区分 | 発見 | モデル化 | 詳細図掲載 | 除外 |
|---|---:|---:|---:|---:|
| ソースファイル | 13 | 13 | - | 0 |
| クラス | 7 | 7 | 7 | 0 |
| メソッド | 53 | 53 | 53 | 0 |
| 関数 | 152 | 152 | 152 | 0 |

## 図の使い分け

| 図 | 内容 |
|---|---|
| [`00-repository-overview.puml`](00-repository-overview.puml) | 起動元、Worker、同期・ジョブ、状態、外部 API の全体像と標準順序 |
| [`01-context-trust.puml`](01-context-trust.puml) | Cloudflare と外部サービス間の信頼境界 |
| [`02-api-dependencies.puml`](02-api-dependencies.puml) | 主要同期・認証・watch の API 入出力 |
| [`03-module-dependencies.puml`](03-module-dependencies.puml) | リポジトリ内の静的 import 依存 |
| [`apis/discord-sync.puml`](apis/discord-sync.puml) | 詳細図 |
| [`apis/google-apply.puml`](apis/google-apply.puml) | 詳細図 |
| [`apis/google-auth.puml`](apis/google-auth.puml) | 詳細図 |
| [`apis/google-calendar.puml`](apis/google-calendar.puml) | 詳細図 |
| [`apis/health-jobs.puml`](apis/health-jobs.puml) | 疎通確認と定期ジョブの API 入出力 |
| [`boundaries/cloudflare-state.puml`](boundaries/cloudflare-state.puml) | 詳細図 |
| [`classes/00-class-index.puml`](classes/00-class-index.puml) | 全 7 クラスの分割先索引 |
| [`classes/application.puml`](classes/application.puml) | クラス役割と依存の詳細 |
| [`classes/runtime-stubs.puml`](classes/runtime-stubs.puml) | クラス役割と依存の詳細 |
| [`classes/tooling.puml`](classes/tooling.puml) | クラス役割と依存の詳細 |
| [`flows/google-webhook.puml`](flows/google-webhook.puml) | Webhook の重複抑止と同期開始順序 |
| [`flows/scheduled.puml`](flows/scheduled.puml) | Cron で同期、watch、3ジョブを実行する順序 |
| [`flows/sync-dispatch.puml`](flows/sync-dispatch.puml) | 同期ディスパッチの排他、取得、反映、確定順序 |
| [`methods/00-method-index.puml`](methods/00-method-index.puml) | 全 53 メソッド・152 関数のモジュール別件数と分割先 |
| [`methods/discord-sync-01.puml`](methods/discord-sync-01.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/discord-sync-02.puml`](methods/discord-sync-02.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/discord-sync-03.puml`](methods/discord-sync-03.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/entry-01.puml`](methods/entry-01.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/entry-02.puml`](methods/entry-02.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/google-apply-01.puml`](methods/google-apply-01.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/google-apply-02.puml`](methods/google-apply-02.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/google-apply-03.puml`](methods/google-apply-03.puml) | モジュール内シンボル詳細（9 件） |
| [`methods/google-auth-01.puml`](methods/google-auth-01.puml) | モジュール内シンボル詳細（9 件） |
| [`methods/google-auth-02.puml`](methods/google-auth-02.puml) | モジュール内シンボル詳細（8 件） |
| [`methods/google-calendar.puml`](methods/google-calendar.puml) | モジュール内シンボル詳細（6 件） |
| [`methods/google-watch.puml`](methods/google-watch.puml) | モジュール内シンボル詳細（8 件） |
| [`methods/health-checks.puml`](methods/health-checks.puml) | モジュール内シンボル詳細（6 件） |
| [`methods/jobs-01.puml`](methods/jobs-01.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/jobs-02.puml`](methods/jobs-02.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/runtime-stubs.puml`](methods/runtime-stubs.puml) | モジュール内シンボル詳細（4 件） |
| [`methods/state-01.puml`](methods/state-01.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/state-02.puml`](methods/state-02.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/state-03.puml`](methods/state-03.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/sync-lock.puml`](methods/sync-lock.puml) | モジュール内シンボル詳細（3 件） |
| [`methods/utf8-tool.puml`](methods/utf8-tool.puml) | モジュール内シンボル詳細（4 件） |
| [`methods/validator-01.puml`](methods/validator-01.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/validator-02.puml`](methods/validator-02.puml) | モジュール内シンボル詳細（9 件） |
| [`modules/runtime-dependencies.puml`](modules/runtime-dependencies.puml) | 詳細図 |

## ローカル検証と再描画

```bash
source .venv/bin/activate
python tools/validate_plantuml.py docs/architecture/plantuml \
  --plantuml-jar artifacts/tools/plantuml/plantuml-lgpl-1.2026.7.jar \
  --require-plantuml --render-svg
```

PlantUML JAR は `1.2026.7`、SHA-256 は `40b8173a88ae408a382555a7bbee2f785e79b7493ac5d780bc637402cc213f60` に固定する。公開 PlantUML サーバーやリモート include は使用しない。

## モジュール・API・状態

| ID | 種別 | 名前 | 役割 | 根拠 | 確実性 | 図 |
|---|---|---|---|---|---|---|
| P001 | component | `workers/src/entry.py` | HTTP、Cron、認可、同期とジョブの起動を一つの Worker エントリで調整する。 | workers/src/entry.py:1-694 | confirmed | 03-module-dependencies.puml、classes/application.puml、methods/00-method-index.puml、methods/entry-01.puml、methods/entry-02.puml、modules/runtime-dependencies.puml |
| P002 | component | `workers/src/google_calendar_sync.py` | Google Calendar の差分イベントを取得し、次回カーソル候補を返す。 | workers/src/google_calendar_sync.py:1-187 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/google-calendar.puml、methods/00-method-index.puml、methods/google-calendar.puml、modules/runtime-dependencies.puml |
| P003 | component | `workers/src/google_apply_sync.py` | Google Calendar の変更を Notion と Discord へ反映する。 | workers/src/google_apply_sync.py:1-884 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/google-apply.puml、methods/00-method-index.puml、methods/google-apply-01.puml、methods/google-apply-02.puml、methods/google-apply-03.puml、modules/runtime-dependencies.puml |
| P004 | component | `workers/src/discord_notion_sync.py` | Discord Scheduled Events の差分を Notion と Google Calendar へ反映する。 | workers/src/discord_notion_sync.py:1-960 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/discord-sync.puml、methods/00-method-index.puml、methods/discord-sync-01.puml、methods/discord-sync-02.puml、methods/discord-sync-03.puml、modules/runtime-dependencies.puml |
| P005 | component | `workers/src/google_auth.py` | Google API 用アクセストークンを設定、キャッシュ、ブローカー、サービスアカウントから解決する。 | workers/src/google_auth.py:1-509 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/discord-sync.puml、apis/google-auth.puml、apis/google-calendar.puml、methods/00-method-index.puml、methods/google-auth-01.puml、methods/google-auth-02.puml、modules/runtime-dependencies.puml |
| P006 | component | `workers/src/google_watch.py` | Google Calendar watch を登録・更新し、有効期限内の状態を維持する。 | workers/src/google_watch.py:1-240 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/google-calendar.puml、flows/scheduled.puml、methods/00-method-index.puml、methods/google-watch.puml、modules/runtime-dependencies.puml |
| P007 | component | `workers/src/health_checks.py` | Google、Notion、Discord への認証付き疎通結果をまとめる。 | workers/src/health_checks.py:1-160 | confirmed | 03-module-dependencies.puml、apis/health-jobs.puml、methods/00-method-index.puml、methods/health-checks.puml、modules/runtime-dependencies.puml |
| P008 | component | `workers/src/jobs.py` | Q&A 通知、前日リマインド、終了済みページ整理の定期ジョブを実行する。 | workers/src/jobs.py:1-554 | confirmed | 00-repository-overview.puml、01-context-trust.puml、03-module-dependencies.puml、apis/health-jobs.puml、methods/00-method-index.puml、methods/jobs-01.puml、methods/jobs-02.puml、modules/runtime-dependencies.puml |
| P009 | component | `workers/src/state.py` | Workers KV と Durable Object の状態アクセスを責務別に集約する。 | workers/src/state.py:1-303 | confirmed | 00-repository-overview.puml、01-context-trust.puml、03-module-dependencies.puml、boundaries/cloudflare-state.puml、classes/application.puml、methods/00-method-index.puml、methods/state-01.puml、methods/state-02.puml、methods/state-03.puml |
| P010 | component | `workers/src/sync_lock_do.py` | 同期ロック、最終同期時刻、Webhook 重複判定を Durable Object で直列化する。 | workers/src/sync_lock_do.py:1-205 | confirmed | 03-module-dependencies.puml、boundaries/cloudflare-state.puml、classes/application.puml、flows/sync-dispatch.puml、methods/00-method-index.puml、methods/sync-lock.puml、modules/runtime-dependencies.puml |
| P011 | component | `tools/validate_plantuml.py` | モデル、カタログ、図、ローカル PlantUML 描画を一括検証する。 | tools/validate_plantuml.py:1-815 | confirmed | classes/tooling.puml、methods/00-method-index.puml、methods/validator-01.puml、methods/validator-02.puml |
| P012 | component | `tools/utf8-no-bom.ps1` | Git 対象ファイルの文字コードを検査し、指定時だけ UTF-8 BOM なしへ変換する。 | tools/utf8-no-bom.ps1:1-155 | confirmed | methods/00-method-index.puml、methods/utf8-tool.puml |
| P013 | component | `typings/workers/__init__.pyi` | Cloudflare Python Workers 実行時 API の静的型境界を宣言する。 | typings/workers/__init__.pyi:1-24 | confirmed | classes/runtime-stubs.puml、methods/00-method-index.puml、methods/runtime-stubs.puml、modules/runtime-dependencies.puml |
| P014 | component | `外部同期サブシステム` | Google、Notion、Discord 間のイベント同期処理を論理的にまとめる。 | workers/src/google_calendar_sync.py:118-187、workers/src/google_apply_sync.py:599-884、workers/src/discord_notion_sync.py:808-960 | inferred | 00-repository-overview.puml、01-context-trust.puml、flows/google-webhook.puml、flows/scheduled.puml |
| P015 | component | `外部 API 群` | Google、Notion、Discord と任意の token broker を概要図上でまとめる。 | workers/src/google_calendar_sync.py:62-187、workers/src/google_apply_sync.py:194-596、workers/src/discord_notion_sync.py:213-805、workers/src/google_auth.py:124-450 | inferred | 00-repository-overview.puml、01-context-trust.puml |
| P016 | component | `Cloudflare 永続状態群` | STATE_KV と SyncCoordinator Durable Object storage を概要図上でまとめる。 | workers/wrangler.jsonc:9-23、workers/wrangler.jsonc:66-74、workers/src/state.py:18-303、workers/src/sync_lock_do.py:47-205 | inferred | 00-repository-overview.puml、01-context-trust.puml |
| X001 | actor | `運用クライアント` | 認可付き管理・同期・ジョブルートを呼び出し、処理結果を受け取る。 | workers/src/entry.py:79-228 | inferred | 00-repository-overview.puml、01-context-trust.puml |
| T001 | interface | `Google Calendar push 通知` | 登録済み watch の変更通知を Webhook へ送り、差分同期を起動する。 | workers/src/google_watch.py:90-122、workers/src/entry.py:127-142 | confirmed | 00-repository-overview.puml、01-context-trust.puml、flows/google-webhook.puml |
| T002 | interface | `Cloudflare Cron Trigger` | Wrangler のスケジュールで scheduled ハンドラを起動する。 | workers/wrangler.jsonc:61-65、workers/src/entry.py:241-343 | confirmed | 00-repository-overview.puml、01-context-trust.puml、flows/scheduled.puml |
| A001 | api | `Worker HTTP API` | ヘルス、Webhook、同期、管理、ジョブの HTTP 要求をルーティングする。 | workers/src/entry.py:57-239 | confirmed | 00-repository-overview.puml、01-context-trust.puml、flows/google-webhook.puml、flows/sync-dispatch.puml |
| A002 | api | `Google Calendar API` | カレンダーイベント、watch、カレンダー情報の参照と更新を提供する。 | workers/src/google_calendar_sync.py:62-115、workers/src/google_watch.py:39-166、workers/src/discord_notion_sync.py:601-659 | confirmed | 02-api-dependencies.puml、apis/discord-sync.puml、apis/google-calendar.puml、apis/health-jobs.puml |
| A003 | api | `Google OAuth token endpoint` | サービスアカウント JWT assertion を検証し、アクセストークンを発行する。 | workers/src/google_auth.py:392-450 | confirmed | 02-api-dependencies.puml、apis/google-auth.puml |
| A004 | api | `設定可能な Google token broker` | 実行時 URL が設定された場合に Calendar スコープのトークンを返す。 | workers/src/google_auth.py:124-176 | runtime-unverified | 02-api-dependencies.puml、apis/google-auth.puml |
| A005 | api | `Notion API` | イベントと Q&A データベースの検索、ページ作成・更新・アーカイブを提供する。 | workers/src/google_apply_sync.py:194-408、workers/src/jobs.py:125-183 | confirmed | 02-api-dependencies.puml、apis/discord-sync.puml、apis/google-apply.puml、apis/health-jobs.puml |
| A006 | api | `Discord REST API` | Scheduled Events、メッセージ、リアクション、接続診断を提供する。 | workers/src/discord_notion_sync.py:213-304、workers/src/jobs.py:186-236 | confirmed | 02-api-dependencies.puml、apis/discord-sync.puml、apis/google-apply.puml、apis/health-jobs.puml |
| D001 | datastore | `Cloudflare Workers KV binding STATE_KV` | 同期カーソル、対応表、キュー、キャッシュ、watch 状態、診断結果を保持する。 | workers/wrangler.jsonc:66-74、workers/src/state.py:83-275 | confirmed | 02-api-dependencies.puml、apis/google-auth.puml、apis/health-jobs.puml、boundaries/cloudflare-state.puml |
| D002 | datastore | `Durable Object storage for SyncCoordinator/global` | 同期ロック、最終同期時刻、期限付き Webhook 重複キーを保持する。 | workers/wrangler.jsonc:9-23、workers/src/sync_lock_do.py:57-205 | confirmed | boundaries/cloudflare-state.puml |

## クラス（全件）

| ID | 完全名 | 役割 | 根拠 | 確実性 | 詳細図 |
|---|---|---|---|---|---|
| C001 | `entry.Default` | HTTP ルーティング、Cron 実行、認可、同期ディスパッチを Worker の入口として調整する。 | workers/src/entry.py:49-694 | confirmed | 00-repository-overview.puml、01-context-trust.puml、classes/00-class-index.puml、classes/application.puml、flows/google-webhook.puml、flows/scheduled.puml、flows/sync-dispatch.puml、methods/entry-01.puml、methods/entry-02.puml |
| C002 | `state.StateStore` | Workers KV への状態アクセスを集約し、整合性が必要な状態を Durable Object へ委譲する。 | workers/src/state.py:18-303 | confirmed | classes/00-class-index.puml、classes/application.puml、flows/google-webhook.puml、flows/scheduled.puml、flows/sync-dispatch.puml、methods/state-01.puml、methods/state-02.puml、methods/state-03.puml |
| C003 | `sync_lock_do.SyncCoordinator` | 同期ロック、最終同期時刻、Webhook 重複抑止を Durable Object 上で直列化する。 | workers/src/sync_lock_do.py:47-205 | confirmed | classes/00-class-index.puml、classes/application.puml、methods/sync-lock.puml |
| C004 | `tools.validate_plantuml.Findings` | PlantUML 成果物の検証中に見つかったエラーと警告を分類して保持する。 | tools/validate_plantuml.py:60-69 | confirmed | classes/00-class-index.puml、classes/tooling.puml、methods/validator-01.puml |
| C005 | `workers.Response` | Cloudflare Workers の HTTP 応答を表し、本文、状態、ヘッダを保持する。 | typings/workers/__init__.pyi:3-15 | runtime-unverified | classes/00-class-index.puml、classes/application.puml、classes/runtime-stubs.puml、methods/runtime-stubs.puml |
| C006 | `workers.WorkerEntrypoint` | Cloudflare Python Worker のエントリポイント基底型を表す。 | typings/workers/__init__.pyi:17-18 | runtime-unverified | classes/00-class-index.puml、classes/application.puml、classes/runtime-stubs.puml |
| C007 | `workers.DurableObject` | Cloudflare Durable Object の基底型と実行時状態を表す。 | typings/workers/__init__.pyi:20-22 | runtime-unverified | classes/00-class-index.puml、classes/application.puml、classes/runtime-stubs.puml |

## メソッド（全件）

| ID | 完全名 | 役割 | 入力 | 出力 | 副作用 | 根拠 | 詳細図 |
|---|---|---|---|---|---|---|---|
| M001 | `entry.Default.fetch` | HTTP エンドポイントを振り分ける。 | request | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/entry.py:57-239 | methods/entry-01.puml |
| M002 | `entry.Default.scheduled` | Cron Trigger 実行エントリ。 | controller、env、ctx | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/entry.py:241-343 | methods/entry-01.puml |
| M003 | `entry.Default._run_sync_dispatch` | 同期処理の中核ディスパッチ。 | request、state、source | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/entry.py:363-463 | methods/entry-01.puml |
| M004 | `sync_lock_do.SyncCoordinator.fetch` | POST body(JSON) の action に応じてロック操作を実行する。 | request | 処理結果 | 外部 HTTP 要求、状態またはファイルの更新、外部 API 呼び出し、永続状態の参照または更新 | workers/src/sync_lock_do.py:57-205 | methods/sync-lock.puml |
| M005 | `tools.validate_plantuml.Findings.__init__` | エラー一覧と警告一覧を空の状態で初期化する。 | なし | None | なし | tools/validate_plantuml.py:61-63 | methods/validator-01.puml |
| M006 | `tools.validate_plantuml.Findings.error` | 検証エラーをエラー一覧へ追加する。 | message | None | 検査結果の収集 | tools/validate_plantuml.py:65-66 | methods/validator-01.puml |
| M007 | `tools.validate_plantuml.Findings.warn` | 検証警告を警告一覧へ追加する。 | message | None | 検査結果の収集 | tools/validate_plantuml.py:68-69 | methods/validator-01.puml |
| M008 | `workers.Response.__init__` | HTTP 応答の本文、状態コード、ヘッダを初期化する。 | body、status、headers | None | なし | typings/workers/__init__.pyi:6-12 | methods/runtime-stubs.puml |
| M009 | `workers.Response.text` | HTTP 応答本文を文字列として返す実行時契約を表す。 | なし | str | なし | typings/workers/__init__.pyi:14-14 | methods/runtime-stubs.puml |
| M010 | `workers.Response.json` | HTTP 応答本文を JSON として返す実行時契約を表す。 | なし | Any | なし | typings/workers/__init__.pyi:15-15 | methods/runtime-stubs.puml |
| M011 | `entry.Default._authorized` | Bearer 認可判定。 | request | bool | なし | workers/src/entry.py:345-361 | methods/entry-01.puml |
| M012 | `entry.Default._sync_interval_seconds` | 同期クールダウン秒数を返す。 | なし | float | なし | workers/src/entry.py:465-471 | methods/entry-01.puml |
| M013 | `entry.Default._sync_all_mode` | 同期モード名を返す。 | なし | str | なし | workers/src/entry.py:473-475 | methods/entry-01.puml |
| M014 | `entry.Default._sync_all_include_discord_notion` | /sync/all で Discord->Notion を実行するか。 | なし | bool | なし | workers/src/entry.py:477-482 | methods/entry-02.puml |
| M015 | `entry.Default._durable_lock_enabled` | Durable Object ロック有効/無効。 | なし | bool | なし | workers/src/entry.py:484-486 | methods/entry-02.puml |
| M016 | `entry.Default._acquire_sync_lock` | SyncCoordinator Durable Object で排他ロックを取得する。 | source | 処理結果 | なし | workers/src/entry.py:488-528 | methods/entry-02.puml |
| M017 | `entry.Default._release_sync_lock` | 取得済みロックを解放する。 | owner | なし | なし | workers/src/entry.py:530-549 | methods/entry-02.puml |
| M018 | `entry.Default._sync_lock_ttl_seconds` | ロック TTL を秒で返す（最小 10 秒）。 | なし | float | なし | workers/src/entry.py:551-557 | methods/entry-02.puml |
| M019 | `entry.Default._migration_status` | 運用診断用ステータスを組み立てる。 | state、include_checks | dict | なし | workers/src/entry.py:559-619 | methods/entry-02.puml |
| M020 | `entry.Default._sync_lock_status` | Durable Object から現在のロック状態を取得する。 | なし | 処理結果 | なし | workers/src/entry.py:621-645 | methods/entry-02.puml |
| M021 | `entry.Default._get_sync_stub` | Durable Object namespace から "global" stub を取得(DO生成)する。 | do_ns | 処理結果 | なし | workers/src/entry.py:648-658 | methods/entry-02.puml |
| M022 | `entry.Default._do_stub_fetch` | Durable Object stub.fetch の呼び出し差分を吸収する。 | stub、url、method、headers、body | 処理結果 | なし | workers/src/entry.py:661-680 | methods/entry-02.puml |
| M023 | `entry.Default._to_bool_query` | クエリ文字列中の bool 値（1/true/yes/on）を判定する。 | query_string、key | bool | なし | workers/src/entry.py:683-694 | methods/entry-02.puml |
| M024 | `state.StateStore.__init__` | インスタンスが利用する依存状態を初期化する。 | env | なし | なし | workers/src/state.py:27-28 | methods/state-01.puml |
| M025 | `state.StateStore.enabled` | STATE_KV バインディングの有無を返す。 | なし | bool | なし | workers/src/state.py:30-32 | methods/state-01.puml |
| M026 | `state.StateStore._kv` | 内部ヘルパー: KV バインディングを返す。 | なし | 処理結果 | なし | workers/src/state.py:34-36 | methods/state-01.puml |
| M027 | `state.StateStore._sync_do` | 内部ヘルパー: SyncCoordinator Durable Object namespace を返す。 | なし | 処理結果 | なし | workers/src/state.py:38-40 | methods/state-01.puml |
| M028 | `state.StateStore._sync_do_stub` | Durable Object namespace から global stub を取得する。 | do_ns | 処理結果 | なし | workers/src/state.py:43-53 | methods/state-01.puml |
| M029 | `state.StateStore._sync_do_fetch` | SyncCoordinator へ JSON POST し、結果辞書を返す。 | stub、action、payload | 処理結果 | なし | workers/src/state.py:56-81 | methods/state-01.puml |
| M030 | `state.StateStore.get_text` | KV から文字列を取得し、空文字は None として扱う。 | key | str \| None | なし | workers/src/state.py:83-92 | methods/state-01.puml |
| M031 | `state.StateStore.put_text` | KV へ文字列を書き込む。 | key、value | なし | 状態またはファイルの更新、状態または外部資源の更新 | workers/src/state.py:94-99 | methods/state-01.puml |
| M032 | `state.StateStore.put_text_if_changed` | 現在値と異なる場合だけ KV へ文字列を書き込む。 | key、value | bool | 状態または外部資源の更新 | workers/src/state.py:101-108 | methods/state-01.puml |
| M033 | `state.StateStore.get_json` | KV の JSON 文字列を辞書等へ復元する。 | key、default | 処理結果 | なし | workers/src/state.py:110-118 | methods/state-02.puml |
| M034 | `state.StateStore.put_json` | Python オブジェクトを JSON 化して KV へ保存する。 | key、payload | なし | 状態または外部資源の更新 | workers/src/state.py:120-125 | methods/state-02.puml |
| M035 | `state.StateStore.put_json_if_changed` | 現在値と異なる場合だけ JSON を KV へ保存する。 | key、payload | bool | 状態または外部資源の更新 | workers/src/state.py:127-134 | methods/state-02.puml |
| M036 | `state.StateStore.mark_google_message_seen` | Google webhook 重複通知抑止用。 | channel_id、message_number | bool | なし | workers/src/state.py:136-165 | methods/state-02.puml |
| M037 | `state.StateStore.get_sync_updated_min` | Google差分同期カーソル(updatedMin)を取得する。 | なし | str \| None | 状態または外部資源の更新 | workers/src/state.py:167-169 | methods/state-02.puml |
| M038 | `state.StateStore.set_sync_updated_min` | Google差分同期カーソル(updatedMin)を保存する。 | updated_min | なし | 状態または外部資源の更新 | workers/src/state.py:171-174 | methods/state-02.puml |
| M039 | `state.StateStore.get_sync_last_epoch` | 最後に同期成功した時刻(epoch秒)を取得する。 | なし | float | なし | workers/src/state.py:176-192 | methods/state-02.puml |
| M040 | `state.StateStore.set_sync_last_epoch_now` | 最後の同期時刻を現在時刻で更新する。 | なし | なし | 状態または外部資源の更新 | workers/src/state.py:194-202 | methods/state-02.puml |
| M041 | `state.StateStore.should_skip_sync_by_cooldown` | クールダウン判定。 | interval_seconds | bool | なし | workers/src/state.py:204-210 | methods/state-02.puml |
| M042 | `state.StateStore.get_gcal_discord_map` | GoogleイベントID -> DiscordイベントID の対応表を取得する。 | なし | dict | なし | workers/src/state.py:212-215 | methods/state-02.puml |
| M043 | `state.StateStore.set_gcal_discord_map` | GoogleイベントID -> DiscordイベントID の対応表を保存する。 | data | なし | 状態または外部資源の更新 | workers/src/state.py:217-219 | methods/state-02.puml |
| M044 | `state.StateStore.get_gcal_notion_map` | GoogleイベントID -> NotionページID の対応表を取得する。 | なし | dict | なし | workers/src/state.py:221-228 | methods/state-03.puml |
| M045 | `state.StateStore.set_gcal_notion_map` | GoogleイベントID -> NotionページID の対応表を保存する。 | data | なし | 状態または外部資源の更新 | workers/src/state.py:230-235 | methods/state-03.puml |
| M046 | `state.StateStore.get_discord_snapshot` | Discordポーリング差分検知用スナップショットを取得する。 | なし | dict | なし | workers/src/state.py:237-240 | methods/state-03.puml |
| M047 | `state.StateStore.set_discord_snapshot` | Discordポーリング差分検知用スナップショットを保存する。 | data | なし | 状態または外部資源の更新 | workers/src/state.py:242-244 | methods/state-03.puml |
| M048 | `state.StateStore.set_last_result` | ジョブ/同期結果を `result:<op_name>` に保存する。 | op_name、payload | なし | 状態または外部資源の更新 | workers/src/state.py:246-268 | methods/state-03.puml |
| M049 | `state.StateStore.get_last_result` | `result:<op_name>` の最新結果を取得する。 | op_name | 処理結果 | なし | workers/src/state.py:270-275 | methods/state-03.puml |
| M050 | `state.StateStore.result_write_min_interval_seconds` | 同一内容の last_result を再保存する最小間隔を返す。 | env | float | なし | workers/src/state.py:278-284 | methods/state-03.puml |
| M051 | `state.StateStore.google_message_dedupe_ttl_seconds` | Google webhook 重複抑止の保持秒数を返す。 | env | float | なし | workers/src/state.py:287-293 | methods/state-03.puml |
| M052 | `state.StateStore.is_kv_sync_cooldown_enabled` | 同期クールダウン機能の有効/無効を返す。 | env | bool | なし | workers/src/state.py:296-298 | methods/state-03.puml |
| M053 | `state.StateStore.is_gcal_dedupe_enabled` | Google webhook 重複抑止機能の有効/無効を返す。 | env | bool | なし | workers/src/state.py:301-303 | methods/state-03.puml |

## 関数（全件）

| ID | 完全名 | 役割 | 入力 | 出力 | 副作用 | 根拠 | 詳細図 |
|---|---|---|---|---|---|---|---|
| F001 | `google_calendar_sync.run_google_delta_fetch` | Googleカレンダーの差分イベントを取る KV の同期カーソル(updated_min)を更新する ただし Notion/Discord への同期はまだやらない | env、state、commit_cursor | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/google_calendar_sync.py:118-187 | flows/sync-dispatch.puml、methods/google-calendar.puml |
| F002 | `google_apply_sync.apply_google_events` | Google Calendar のイベント一覧を受け取り、Notion と Discord に反映する。 | env、state、events | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/google_apply_sync.py:599-884 | flows/sync-dispatch.puml、methods/google-apply-03.puml |
| F003 | `discord_notion_sync.run_discord_notion_poll_sync` | 定期ポーリングのメイン処理。 | env、state | 処理結果 | 標準出力、外部 API 呼び出し、永続状態の参照または更新 | workers/src/discord_notion_sync.py:808-960 | flows/sync-dispatch.puml、methods/discord-sync-03.puml |
| F004 | `google_auth.get_google_access_token` | Google API アクセストークン取得試行の順番: 1) GOOGLE_API_BEARER_TOKEN (直接) 2) KVキャッシュ(google:access_token / google:expires_at) 3) GOOGLE_TOKEN_BROKER_URL 4) サービスアカウントJWTア | env、state | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/google_auth.py:453-475 | methods/google-auth-02.puml |
| F005 | `google_watch.ensure_watch_active` | watch が有効な状態を保つ。 | env、state | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/google_watch.py:197-240 | methods/google-watch.puml |
| F006 | `health_checks.run_connectivity_checks` | 3サービスの疎通確認をまとめて実行し、結果を返す。 | env、state | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/health_checks.py:149-160 | methods/health-checks.puml |
| F007 | `jobs.run_qa_notification_job` | QA通知ジョブ本体。 | env、state、return_detail | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/jobs.py:272-342 | flows/scheduled.puml、methods/jobs-02.puml |
| F008 | `jobs.run_day_before_reminder_job` | 前日リマインド。 | env、state、return_detail | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/jobs.py:369-456 | flows/scheduled.puml、methods/jobs-02.puml |
| F009 | `jobs.run_auto_clean_job` | Notion cleanup ジョブ本体。 | env、state、return_detail | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/jobs.py:505-554 | flows/scheduled.puml、methods/jobs-02.puml |
| F010 | `tools.utf8-no-bom.Get-GitFiles` | 追跡済みまたは未追跡を含む Git 対象ファイル一覧を取得する。 | WithUntracked | ファイルパス一覧 | Git コマンド実行 | tools/utf8-no-bom.ps1:10-18 | methods/utf8-tool.puml |
| F011 | `tools.utf8-no-bom.Test-IsLikelyBinary` | 拡張子と NUL バイトからバイナリらしいファイルかを判定する。 | Path、Bytes | bool | なし | tools/utf8-no-bom.ps1:20-42 | methods/utf8-tool.puml |
| F012 | `tools.utf8-no-bom.Try-DecodeUtf8Strict` | 入力バイト列を厳密な UTF-8 として復号できるかを結果辞書で返す。 | Bytes | 復号結果辞書 | なし | tools/utf8-no-bom.ps1:44-52 | methods/utf8-tool.puml |
| F013 | `tools.utf8-no-bom.Convert-FileToUtf8NoBom` | 対象ファイルを検査し、指定時だけ UTF-8 BOM なしへ変換する。 | Path、DoApply、AllowCp932Fallback | 変換状態辞書 | ファイル更新 | tools/utf8-no-bom.ps1:54-113 | methods/utf8-tool.puml |
| F014 | `tools.validate_plantuml.is_non_empty_string` | 値が空でない文字列かを判定する。 | value | bool | なし | tools/validate_plantuml.py:72-73 | methods/validator-01.puml |
| F015 | `tools.validate_plantuml.normalized_text` | 比較用に記号と大小文字の差を除いた文字列へ正規化する。 | value | str | なし | tools/validate_plantuml.py:76-77 | methods/validator-01.puml |
| F016 | `tools.validate_plantuml.validate_source_refs` | 根拠参照が非空かつリポジトリ相対かを検査する。 | value、location、findings | None | 検査結果の収集 | tools/validate_plantuml.py:80-96 | methods/validator-01.puml |
| F017 | `tools.validate_plantuml.validate_evidence` | 根拠種別と確実性が許可値かを検査する。 | item、location、findings | None | 検査結果の収集 | tools/validate_plantuml.py:99-109 | methods/validator-01.puml |
| F018 | `tools.validate_plantuml.require_fields` | モデル項目に必須フィールドが揃うかを検査する。 | item、required、location、findings | bool | なし | tools/validate_plantuml.py:112-126 | methods/validator-01.puml |
| F019 | `tools.validate_plantuml.validate_nonnegative_integer` | 網羅数が 0 以上の整数かを検査する。 | value、location、findings | int \| None | 検査結果の収集 | tools/validate_plantuml.py:129-137 | methods/validator-01.puml |
| F020 | `tools.validate_plantuml.validate_exclusions` | 除外項目の件数、理由、対象指定を検査して合計する。 | value、location、findings | int | 検査結果の収集 | tools/validate_plantuml.py:140-171 | methods/validator-01.puml |
| F021 | `tools.validate_plantuml.validate_coverage` | ソースと全クラス・メソッド・関数の網羅数を相互照合する。 | value、nodes、model_path、findings | None | 検査結果の収集 | tools/validate_plantuml.py:174-256 | methods/validator-02.puml |
| F022 | `tools.validate_plantuml.validate_exchange_edges` | 双方向エッジの向き、入出力、図上ラベルを照合する。 | edges、diagram_text、findings | None | 検査結果の収集 | tools/validate_plantuml.py:259-328 | methods/validator-02.puml |
| F023 | `tools.validate_plantuml.validate_model` | JSON モデル全体をカタログおよび図の内容と照合する。 | model_path、catalog_text、diagram_text、diagram_texts、findings | None | 検査結果の収集 | tools/validate_plantuml.py:331-536 | methods/validator-02.puml |
| F024 | `tools.validate_plantuml.resolve_local_include` | PlantUML include を成果物ルート内のローカルパスへ解決する。 | source、raw_value、root | Path \| None | なし | tools/validate_plantuml.py:539-550 | methods/validator-02.puml |
| F025 | `tools.validate_plantuml.validate_puml` | 全 PlantUML ソースの安全性、構造、表記を検査する。 | root、findings | tuple[list[Path], str, dict[str, str]] | 検査結果の収集 | tools/validate_plantuml.py:553-623 | methods/validator-02.puml |
| F026 | `tools.validate_plantuml.plantuml_base_command` | ローカル JAR または実行ファイルから安全設定付きコマンドを組み立てる。 | root、plantuml_jar | tuple[list[str] \| None, dict[str, str]] | なし | tools/validate_plantuml.py:626-654 | methods/validator-02.puml |
| F027 | `tools.validate_plantuml.run_plantuml` | 全図の構文を検査し、指定時は階層を保って SVG を描画する。 | diagrams、root、plantuml_jar、render_svg、require_plantuml、findings | None | 外部プロセスまたは HTTP 要求 | tools/validate_plantuml.py:657-721 | methods/validator-02.puml |
| F028 | `tools.validate_plantuml.parse_args` | 検証対象、PlantUML 実体、描画オプションをコマンドラインから読む。 | なし | argparse.Namespace | なし | tools/validate_plantuml.py:724-745 | methods/validator-02.puml |
| F029 | `tools.validate_plantuml.main` | 成果物検証を順に実行し、警告とエラー件数に応じた終了コードを返す。 | なし | int | 標準出力 | tools/validate_plantuml.py:748-811 | methods/validator-02.puml |
| F030 | `workers.fetch` | Cloudflare Workers 実行時の外部 HTTP fetch 契約を表す。 | url、options、**kwargs | Any | 外部 HTTP 要求 | typings/workers/__init__.pyi:24-24 | methods/runtime-stubs.puml |
| F031 | `discord_notion_sync.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/discord_notion_sync.py:11-21 | methods/discord-sync-01.puml |
| F032 | `discord_notion_sync._env_text` | Worker env から文字列設定を安全に取得する。 | env、key、default | str | なし | workers/src/discord_notion_sync.py:34-45 | methods/discord-sync-01.puml |
| F033 | `discord_notion_sync._prop` | Notion プロパティ名の解決ヘルパー。 | env、key、default | str | なし | workers/src/discord_notion_sync.py:48-53 | methods/discord-sync-01.puml |
| F034 | `discord_notion_sync._notion_headers` | Notion REST API 呼び出しに必要な共通ヘッダを返す。 | env | dict | なし | workers/src/discord_notion_sync.py:56-65 | methods/discord-sync-01.puml |
| F035 | `discord_notion_sync._parse_rfc3339` | RFC3339 文字列を datetime へ変換する。 | value | 処理結果 | なし | workers/src/discord_notion_sync.py:68-78 | methods/discord-sync-01.puml |
| F036 | `discord_notion_sync._notion_extract_rich_text` | Notion ページの rich_text プロパティ先頭要素を文字列として抽出する。 | page、prop_name | 処理結果 | なし | workers/src/discord_notion_sync.py:81-101 | methods/discord-sync-01.puml |
| F037 | `discord_notion_sync._parse_discord_event_times` | Discord Scheduled Event の開始/終了時刻を datetime として返す。 | event | 処理結果 | なし | workers/src/discord_notion_sync.py:104-116 | methods/discord-sync-01.puml |
| F038 | `discord_notion_sync._discord_unix_timestamp` | Discord メッセージ用の Unix timestamp を返す。 | dt | int \| None | なし | workers/src/discord_notion_sync.py:119-128 | methods/discord-sync-01.puml |
| F039 | `discord_notion_sync._date_prop_from_datetimes` | datetime を Notion date プロパティ形式へ変換する。 | start_dt、end_dt | 処理結果 | なし | workers/src/discord_notion_sync.py:131-143 | methods/discord-sync-01.puml |
| F040 | `discord_notion_sync._event_location` | Discord event から location(場所) を抽出する。 | event | 処理結果 | なし | workers/src/discord_notion_sync.py:146-156 | methods/discord-sync-01.puml |
| F041 | `discord_notion_sync._normalize_event` | 差分検知用に Discord event を正規化する。 | event | 処理結果 | なし | workers/src/discord_notion_sync.py:159-175 | methods/discord-sync-01.puml |
| F042 | `discord_notion_sync._fingerprint` | 正規化イベントを JSON 文字列にして指紋化する。 | event | 処理結果 | 標準出力 | workers/src/discord_notion_sync.py:178-186 | methods/discord-sync-02.puml |
| F043 | `discord_notion_sync._snapshot_status` | 保存済みスナップショット文字列から Discord event status を取り出す。 | snapshot_value | str | なし | workers/src/discord_notion_sync.py:189-200 | methods/discord-sync-02.puml |
| F044 | `discord_notion_sync._should_treat_missing_event_as_delete` | 前回スナップショットにしか存在しないイベントを削除扱いにするか判定する。 | snapshot_value | bool | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:203-210 | methods/discord-sync-02.puml |
| F045 | `discord_notion_sync._discord_api_request` | Discord REST API の共通ラッパー。 | env、method、path、payload | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/discord_notion_sync.py:213-254 | methods/discord-sync-02.puml |
| F046 | `discord_notion_sync._list_discord_scheduled_events` | ギルド(サーバ)のイベント一覧を取得する。 | env | 処理結果 | なし | workers/src/discord_notion_sync.py:257-274 | methods/discord-sync-02.puml |
| F047 | `discord_notion_sync._discord_send_message` | Discord チャンネルへ通常メッセージを投稿し、message_id を返す。 | env、channel_id、content、allowed_mentions | str \| None | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:277-291 | methods/discord-sync-02.puml |
| F048 | `discord_notion_sync._discord_add_reaction` | Discord メッセージへリアクションを追加する。 | env、channel_id、message_id、emoji | bool | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:294-304 | methods/discord-sync-02.puml |
| F049 | `discord_notion_sync._notion_query_by_message_id` | Notion DB からメッセージID一致のページを1件取得する。 | env、db_id、message_id | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/discord_notion_sync.py:307-337 | methods/discord-sync-02.puml |
| F050 | `discord_notion_sync._notion_update_event` | Notion イベントページを部分更新する。 | env、page_id、name、content、date_prop、message_id、creator_id、page_uuid、event_url、location、google_event_id | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:340-402 | methods/discord-sync-02.puml |
| F051 | `discord_notion_sync._notion_create_event` | Notion イベントページを新規作成する。 | env、db_id、name、content、date_prop、message_id、creator_id、event_url、location、google_event_id | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:405-472 | methods/discord-sync-02.puml |
| F052 | `discord_notion_sync._notion_archive_page` | Notion ページを archived=true に更新する。 | env、page_id | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:475-490 | methods/discord-sync-02.puml |
| F053 | `discord_notion_sync._discord_event_url` | Discordイベントの公開URLを組み立てる。 | env、event_id | 処理結果 | なし | workers/src/discord_notion_sync.py:493-500 | methods/discord-sync-03.puml |
| F054 | `discord_notion_sync._build_event_created_message` | 新規作成イベントの通知メッセージ本文を組み立てる。 | env、event | str \| None | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:503-531 | methods/discord-sync-03.puml |
| F055 | `discord_notion_sync._notify_discord_event_created` | 新規作成された Discord イベントを通知チャンネルへ投稿する。 | env、event | bool | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:534-555 | methods/discord-sync-03.puml |
| F056 | `discord_notion_sync._google_sync_enabled` | Discord -> Google 同期を有効化する条件判定。 | env | bool | なし | workers/src/discord_notion_sync.py:558-568 | methods/discord-sync-03.puml |
| F057 | `discord_notion_sync._google_event_body` | Discordイベント情報を Google Calendar events API 用のボディに変換する。 | name、description、start_dt、end_dt、location、discord_event_id | 処理結果 | なし | workers/src/discord_notion_sync.py:571-598 | methods/discord-sync-03.puml |
| F058 | `discord_notion_sync._google_create_event` | Google Calendar にイベントを新規作成する。 | env、token、payload | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:601-621 | methods/discord-sync-03.puml |
| F059 | `discord_notion_sync._google_update_event` | Google Calendar イベントを PATCH 更新する。 | env、token、google_event_id、payload | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:624-642 | methods/discord-sync-03.puml |
| F060 | `discord_notion_sync._google_delete_event` | Google Calendar イベントを削除する。 | env、token、google_event_id | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:645-659 | methods/discord-sync-03.puml |
| F061 | `discord_notion_sync._sync_discord_event_upsert` | Discordの単一イベントを Notion/Google に同期する。 | env、event、google_token | bool | なし | workers/src/discord_notion_sync.py:662-779 | methods/discord-sync-03.puml |
| F062 | `discord_notion_sync._sync_discord_event_delete` | Discord から削除されたイベントを Google/Notion から除去する。 | env、event_id、google_token | bool | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:782-805 | methods/discord-sync-03.puml |
| F063 | `entry._json_response` | JSON レスポンスを統一フォーマットで返す。 | payload、status | Response | なし | workers/src/entry.py:20-26 | methods/entry-01.puml |
| F064 | `entry._header` | HTTP ヘッダ値を trim して取得する。 | request、name | str \| None | なし | workers/src/entry.py:29-35 | methods/entry-01.puml |
| F065 | `entry._bool_env` | 環境変数文字列を bool として解釈する。 | value、default | bool | なし | workers/src/entry.py:38-42 | methods/entry-01.puml |
| F066 | `entry._detail_dict` | 任意の処理結果を詳細応答用の辞書へ正規化する。 | value | dict[str, Any] | なし | workers/src/entry.py:45-46 | methods/entry-01.puml |
| F067 | `google_apply_sync.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/google_apply_sync.py:8-18 | methods/google-apply-01.puml |
| F068 | `google_apply_sync._env_text` | Worker env から文字列設定を取得する。 | env、key、default | str | なし | workers/src/google_apply_sync.py:30-36 | methods/google-apply-01.puml |
| F069 | `google_apply_sync._env_bool` | Worker env の bool 設定を取得する。 | env、key、default | bool | なし | workers/src/google_apply_sync.py:39-44 | methods/google-apply-01.puml |
| F070 | `google_apply_sync._prop` | Notion プロパティ名の env 上書きを解決する。 | env、key、default | str | なし | workers/src/google_apply_sync.py:47-49 | methods/google-apply-01.puml |
| F071 | `google_apply_sync._notion_headers` | Notion API 共通ヘッダを返す。 | env | dict | なし | workers/src/google_apply_sync.py:52-59 | methods/google-apply-01.puml |
| F072 | `google_apply_sync._parse_rfc3339` | RFC3339 文字列を datetime へ変換する。 | value | 処理結果 | なし | workers/src/google_apply_sync.py:62-69 | methods/google-apply-01.puml |
| F073 | `google_apply_sync._to_discord_iso` | Discord API 向けの UTC ISO8601(Z) へ変換する。 | dt | 処理結果 | なし | workers/src/google_apply_sync.py:72-78 | methods/google-apply-01.puml |
| F074 | `google_apply_sync._parse_google_event_times` | Googleイベントの開始/終了時刻を datetime として返す。 | event | 処理結果 | なし | workers/src/google_apply_sync.py:81-116 | methods/google-apply-01.puml |
| F075 | `google_apply_sync._parse_google_event_times.parse_part` | Google イベントの date または dateTime 部分を日時と終日フラグへ変換する。 | part、is_end | 処理結果 | なし | workers/src/google_apply_sync.py:88-107 | methods/google-apply-01.puml |
| F076 | `google_apply_sync._build_notion_date` | Google の start/end を Notion date 形式へ変換する。 | event | 処理結果 | なし | workers/src/google_apply_sync.py:119-130 | methods/google-apply-01.puml |
| F077 | `google_apply_sync._notion_extract_rich_text` | Notion rich_text の先頭要素を文字列化して返す。 | page、prop_name | 処理結果 | なし | workers/src/google_apply_sync.py:133-148 | methods/google-apply-02.puml |
| F078 | `google_apply_sync._resolve_discord_event_id_for_google_event` | Googleイベントに対応する Discord event id を既知情報から推定する。 | env、google_event_id、notion_page、fallback_page、gcal_discord_map | str \| None | なし | workers/src/google_apply_sync.py:151-173 | methods/google-apply-02.puml |
| F079 | `google_apply_sync._google_private_props` | Google event.extendedProperties.private を辞書で返す。 | event | dict | なし | workers/src/google_apply_sync.py:176-179 | methods/google-apply-02.puml |
| F080 | `google_apply_sync._google_origin_discord_event_id` | Discord 由来で Google に作られたイベントなら元の Discord event id を返す。 | event | str \| None | なし | workers/src/google_apply_sync.py:182-191 | methods/google-apply-02.puml |
| F081 | `google_apply_sync._notion_query_by_google_event_id` | GoogleイベントID一致で Notion ページを1件検索する。 | env、db_id、google_event_id | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_apply_sync.py:194-219 | methods/google-apply-02.puml |
| F082 | `google_apply_sync._notion_query_by_message_id` | message_id一致で Notion ページを1件検索する（外部DB互換用途）。 | env、db_id、message_id | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_apply_sync.py:222-247 | methods/google-apply-02.puml |
| F083 | `google_apply_sync._notion_get_page` | Notion page_id からページを直接取得する。 | env、page_id | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_apply_sync.py:250-263 | methods/google-apply-02.puml |
| F084 | `google_apply_sync._notion_update_event` | Notion イベントページを部分更新する。 | env、page_id、name、content、date_prop、event_url、google_event_id、page_uuid、message_id、location | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/google_apply_sync.py:266-324 | methods/google-apply-02.puml |
| F085 | `google_apply_sync._notion_create_event` | Notion イベントページを新規作成する。 | env、db_id、name、content、date_prop、creator_id、event_url、google_event_id、message_id、location | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/google_apply_sync.py:327-391 | methods/google-apply-02.puml |
| F086 | `google_apply_sync._notion_archive_page` | Notion ページを archived=true に更新(削除)する。 | env、page | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/google_apply_sync.py:394-408 | methods/google-apply-02.puml |
| F087 | `google_apply_sync._discord_api_request` | Discord REST API 共通ラッパー。 | env、method、path、payload | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_apply_sync.py:411-437 | methods/google-apply-03.puml |
| F088 | `google_apply_sync._discord_sync_available` | Discord 反映に必要な設定が揃っているか判定する。 | env | 処理結果 | なし | workers/src/google_apply_sync.py:440-448 | methods/google-apply-03.puml |
| F089 | `google_apply_sync._build_discord_description` | Discord説明文を組み立てる。 | env、description、google_event_id | 処理結果 | なし | workers/src/google_apply_sync.py:451-470 | methods/google-apply-03.puml |
| F090 | `google_apply_sync._build_discord_payload` | Google イベントを Discord イベント payload へ変換する。 | env、event | 処理結果 | なし | workers/src/google_apply_sync.py:473-498 | methods/google-apply-03.puml |
| F091 | `google_apply_sync._discord_create_event` | Discord イベントを新規作成する。 | env、event | 処理結果 | 状態または外部資源の更新 | workers/src/google_apply_sync.py:501-513 | methods/google-apply-03.puml |
| F092 | `google_apply_sync._discord_update_event` | Discord イベントを更新する。 | env、discord_event_id、event | 処理結果 | 状態または外部資源の更新 | workers/src/google_apply_sync.py:516-528 | methods/google-apply-03.puml |
| F093 | `google_apply_sync._discord_delete_event` | Discord イベントを削除する。 | env、discord_event_id | 処理結果 | 状態または外部資源の更新 | workers/src/google_apply_sync.py:531-542 | methods/google-apply-03.puml |
| F094 | `google_apply_sync._sync_to_discord` | Googleイベントを Discord 側へ同期し、DiscordイベントIDを返す。 | env、event、notion_page、fallback_page、gcal_discord_map | 処理結果 | なし | workers/src/google_apply_sync.py:545-596 | methods/google-apply-03.puml |
| F095 | `google_auth.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/google_auth.py:11-21 | methods/google-auth-01.puml |
| F096 | `google_auth._b64url` | JWT 用 Base64URL エンコード（パディングなし）。 | data | str | なし | workers/src/google_auth.py:38-42 | methods/google-auth-01.puml |
| F097 | `google_auth._env_text` | Worker env から文字列を安全に取得する。 | env、key、default | str | なし | workers/src/google_auth.py:45-53 | methods/google-auth-01.puml |
| F098 | `google_auth._get_cached_token` | KV からキャッシュ済みGoogleアクセストークンを取得する。 | state | 処理結果 | なし | workers/src/google_auth.py:56-74 | methods/google-auth-01.puml |
| F099 | `google_auth._get_cached_token_meta` | キャッシュトークンの存在・有効性メタ情報(健康度)を返す。 | state | 処理結果 | なし | workers/src/google_auth.py:77-110 | methods/google-auth-01.puml |
| F100 | `google_auth._save_cached_token` | token と任意の有効期限(epoch)を KV に保存する。 | state、token、expires_at | なし | 状態または外部資源の更新 | workers/src/google_auth.py:113-121 | methods/google-auth-01.puml |
| F101 | `google_auth._fetch_token_from_broker` | 外部トークンブローカーからトークンを取得する。 | env、state | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_auth.py:124-176 | methods/google-auth-01.puml |
| F102 | `google_auth._load_service_account_info_from_env` | Service Account JSON を env から読み込む。 | env | 処理結果 | なし | workers/src/google_auth.py:179-198 | methods/google-auth-01.puml |
| F103 | `google_auth._pem_pkcs8_to_der` | PEM 形式の PKCS8 秘密鍵を DER(bytes) に変換する。 | private_key_pem | 処理結果 | なし | workers/src/google_auth.py:201-215 | methods/google-auth-01.puml |
| F104 | `google_auth._js_uint8_array` | Python bytes を JS Uint8Array に変換する。 | data | 処理結果 | なし | workers/src/google_auth.py:218-229 | methods/google-auth-02.puml |
| F105 | `google_auth._uint8_array_to_bytes` | JS Uint8Array を Python bytes に変換する。 | js_arr | 処理結果 | なし | workers/src/google_auth.py:232-249 | methods/google-auth-02.puml |
| F106 | `google_auth._sign_rs256` | Web Crypto API を使い、JWT 署名対象を RS256 で署名する。 | message、private_key_pem | 処理結果 | なし | workers/src/google_auth.py:252-323 | methods/google-auth-02.puml |
| F107 | `google_auth._build_service_account_assertion` | OAuth JWT Bearer 用 JWT を生成する。 | sa_info、scope | 処理結果 | なし | workers/src/google_auth.py:326-389 | methods/google-auth-02.puml |
| F108 | `google_auth._fetch_token_from_service_account` | JWT アサーションを使ってGoogle の OAuth トークンエンドポイントから アクセストークンを取得する。 | env、state | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_auth.py:392-450 | methods/google-auth-02.puml |
| F109 | `google_auth.describe_google_auth_sources` | 現在利用可能な認証ソース状態を返す。 | env、state | 処理結果 | なし | workers/src/google_auth.py:478-492 | methods/google-auth-02.puml |
| F110 | `google_auth.set_google_access_token` | 管理API経由で受け取ったトークンを KV キャッシュに保存する。 | state、access_token、expires_in_seconds | 処理結果 | 状態または外部資源の更新 | workers/src/google_auth.py:495-509 | methods/google-auth-02.puml |
| F111 | `google_calendar_sync.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/google_calendar_sync.py:11-21 | methods/google-calendar.puml |
| F112 | `google_calendar_sync._env_text` | Worker env から文字列を安全に取得する。 | env、key、default | str | なし | workers/src/google_calendar_sync.py:31-39 | methods/google-calendar.puml |
| F113 | `google_calendar_sync._parse_rfc3339` | RFC3339 文字列を datetime へ変換する。 | value | 処理結果 | なし | workers/src/google_calendar_sync.py:42-52 | methods/google-calendar.puml |
| F114 | `google_calendar_sync._to_rfc3339_z` | 日時を UTC の RFC3339 Z 形式へ変換する。 | dt | str | なし | workers/src/google_calendar_sync.py:55-59 | methods/google-calendar.puml |
| F115 | `google_calendar_sync._google_events_list` | Google Calendar events.list をページングし、イベント一覧を取得する。 | calendar_id、bearer_token、updated_min | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_calendar_sync.py:62-115 | methods/google-calendar.puml |
| F116 | `google_watch.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/google_watch.py:12-22 | methods/google-watch.puml |
| F117 | `google_watch._env_text` | Worker env から文字列設定を取得する。 | env、key、default | str | なし | workers/src/google_watch.py:25-36 | methods/google-watch.puml |
| F118 | `google_watch._watch_call` | Google Calendar watch 関連 API 呼び出しの共通ラッパー。 | env、state、method、path、payload | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_watch.py:39-87 | methods/google-watch.puml |
| F119 | `google_watch.register_watch` | Google Calendar events.watch を新規登録する。 | env、state | 処理結果 | なし | workers/src/google_watch.py:90-122 | methods/google-watch.puml |
| F120 | `google_watch.renew_watch` | 既存 watch を更新する。 | env、state | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_watch.py:125-166 | methods/google-watch.puml |
| F121 | `google_watch._parse_expiration_epoch_seconds` | Google watch expiration (ミリ秒)を桁を見て秒に変換する。 | expiration_value | float | なし | workers/src/google_watch.py:169-182 | methods/google-watch.puml |
| F122 | `google_watch._renew_threshold_seconds` | watch 更新しきい値（秒）を返す。 | env | float | なし | workers/src/google_watch.py:185-194 | methods/google-watch.puml |
| F123 | `health_checks.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/health_checks.py:10-20 | methods/health-checks.puml |
| F124 | `health_checks._env_text` | Worker env から文字列設定を取得する。 | env、key、default | str | なし | workers/src/health_checks.py:29-35 | methods/health-checks.puml |
| F125 | `health_checks.check_notion` | Notion API 疎通確認。 | env | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/health_checks.py:38-71 | methods/health-checks.puml |
| F126 | `health_checks.check_discord` | Discord API 疎通確認。 | env | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/health_checks.py:74-106 | methods/health-checks.puml |
| F127 | `health_checks.check_google_calendar` | Google Calendar API 疎通確認。 | env、state | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/health_checks.py:109-146 | methods/health-checks.puml |
| F128 | `jobs.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/jobs.py:8-18 | methods/jobs-01.puml |
| F129 | `jobs._env_text` | Worker env から文字列設定を取得する。 | env、key、default | str | なし | workers/src/jobs.py:27-33 | methods/jobs-01.puml |
| F130 | `jobs._header_json` | Notion API 用の共通 JSON ヘッダを返す。 | token | dict | なし | workers/src/jobs.py:36-42 | methods/jobs-01.puml |
| F131 | `jobs._parse_rfc3339` | RFC3339 文字列を datetime へ変換する。 | value | 処理結果 | なし | workers/src/jobs.py:45-52 | methods/jobs-01.puml |
| F132 | `jobs._event_location` | Discord イベントから location を抽出する。 | event | 処理結果 | なし | workers/src/jobs.py:55-62 | methods/jobs-01.puml |
| F133 | `jobs._format_japanese_datetime` | 日時を `YYYY年M月D日 曜日 HH:MM` 形式へ整形する。 | dt | str \| None | なし | workers/src/jobs.py:65-78 | methods/jobs-01.puml |
| F134 | `jobs._extract_rich_text` | Notion page の rich_text プロパティ先頭を文字列化して返す。 | page、prop_name | 処理結果 | なし | workers/src/jobs.py:81-96 | methods/jobs-01.puml |
| F135 | `jobs._extract_title` | Notion ページの title プロパティ先頭を文字列化して返す。 | page、prop_name | 処理結果 | なし | workers/src/jobs.py:99-110 | methods/jobs-01.puml |
| F136 | `jobs._extract_number` | Notion ページの number プロパティ値を返す。 | page、prop_name | 処理結果 | なし | workers/src/jobs.py:113-116 | methods/jobs-01.puml |
| F137 | `jobs._extract_date` | Notion ページの date プロパティ(dict)を返す。 | page、prop_name | 処理結果 | なし | workers/src/jobs.py:119-122 | methods/jobs-01.puml |
| F138 | `jobs._notion_query_all_pages` | Notion DB 全件取得（ページネーション対応）。 | env、db_id | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/jobs.py:125-163 | methods/jobs-01.puml |
| F139 | `jobs._notion_patch_page_number` | Q&A ページの `質問番号` を更新する。 | env、page_id、number_value | bool | 外部プロセスまたは HTTP 要求 | workers/src/jobs.py:166-183 | methods/jobs-01.puml |
| F140 | `jobs._discord_api_request` | Discord REST API 共通ラッパー。 | env、method、path、payload | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/jobs.py:186-218 | methods/jobs-02.puml |
| F141 | `jobs._discord_send_message` | Discord チャンネルへメッセージ送信する。 | env、channel_id、content、allowed_mentions | bool | 状態または外部資源の更新 | workers/src/jobs.py:221-236 | methods/jobs-02.puml |
| F142 | `jobs.ensure_qa_question_numbers` | Q&A DB の `質問番号` 欠番を埋める。 | env | なし | なし | workers/src/jobs.py:239-269 | methods/jobs-02.puml |
| F143 | `jobs._list_discord_events` | Discord ギルド(サーバ)のイベント一覧を取得する。 | env | 処理結果 | なし | workers/src/jobs.py:345-358 | methods/jobs-02.puml |
| F144 | `jobs._discord_event_url` | Discord event URL を組み立てる。 | env、event_id | 処理結果 | なし | workers/src/jobs.py:361-366 | methods/jobs-02.puml |
| F145 | `jobs._utc_now` | UTC 現在時刻を返す。 | なし | 処理結果 | なし | workers/src/jobs.py:459-461 | methods/jobs-02.puml |
| F146 | `jobs._notion_archive_page` | Notion ページを archived=true に更新する。 | env、page_id | bool | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/jobs.py:464-478 | methods/jobs-02.puml |
| F147 | `jobs._archive_internal_due` | 内部DBのアーカイブ判定。 | date_obj、now_utc | bool | 状態または外部資源の更新 | workers/src/jobs.py:481-493 | methods/jobs-02.puml |
| F148 | `jobs._cleanup_interval_seconds` | cleanup ジョブ最小実行間隔（秒）を返す。 | env | int | なし | workers/src/jobs.py:496-502 | methods/jobs-02.puml |
| F149 | `state._bool_env` | 環境変数文字列を bool として解釈する。 | value、default | bool | なし | workers/src/state.py:6-10 | methods/state-01.puml |
| F150 | `state._json_text` | JSON 比較/保存用に安定した文字列表現へ正規化する。 | payload | str | なし | workers/src/state.py:13-15 | methods/state-01.puml |
| F151 | `sync_lock_do._decode_lock_record` | storage.get("lock") の返り値を lock 辞書へ正規化する。 | value | dict | なし | workers/src/sync_lock_do.py:8-30 | methods/sync-lock.puml |
| F152 | `sync_lock_do._decode_json_record` | storage 上の JSON 文字列/辞書を dict に正規化する。 | value | dict | なし | workers/src/sync_lock_do.py:33-44 | methods/sync-lock.puml |

## 信頼境界

| ID | 境界 | 理由 | 根拠 | 確実性 |
|---|---|---|---|---|
| Z001 | 外部呼び出し・トリガー | ネットワーク到達性と起動主体が Worker 内部と異なる | `workers/src/entry.py:57-142`、`workers/wrangler.jsonc:61-65` | confirmed |
| Z002 | Cloudflare Worker runtime | Secret 参照と binding 権限を持つ実行境界 | `workers/src/entry.py:49-55`、`workers/wrangler.jsonc:2-7` | confirmed |
| Z003 | Cloudflare managed state | 永続データ責任と分離単位が変わる | `workers/wrangler.jsonc:9-23`、`workers/wrangler.jsonc:66-74` | confirmed |
| Z004 | Google services | OAuth 主体、アカウント、外部運用主体が変わる | `workers/src/google_auth.py:392-450` | confirmed |
| Z005 | Notion service | Integration 主体と Workspace のデータ責任が変わる | `workers/src/google_apply_sync.py:194-408` | confirmed |
| Z006 | Discord service | Bot 主体と Guild/Channel 認可が変わる | `workers/src/discord_notion_sync.py:213-304` | confirmed |
| Z007 | token broker | 運用主体、資格情報移送、ネットワーク境界が実行時設定に依存する | `workers/src/google_auth.py:124-176` | runtime-unverified |

## 未確認事項

1. Cloudflare 上の Secret、実 binding、外部 API 権限、疎通結果はローカル静的検査では確定できない。
2. token broker の設定有無、提供者、実際の認証方式は実環境で確認が必要。
3. `/gcal/webhook` の Edge 側制御と Google watch の現行登録状態は運用環境で確認が必要。
4. 外部 API の作成・更新・削除、レート制限、再試行の実行成功は未検証。
5. `new_sqlite_classes` は Durable Object の移行指定であり、Cloudflare D1 binding を示さない。
