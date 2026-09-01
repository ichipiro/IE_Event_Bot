# PlantUML アーキテクチャ・カタログ

このカタログは、現行チェックアウトの実装・設定・文書を静的に調査し、PlantUML 図と `architecture-index.json` の共通根拠を示す。Cloudflare、Google、Notion、Discord の実環境設定、権限、疎通、実行成功は確認していない。

- 根拠リビジョン: `43fad919c49a83568c629c4b6afea9a1b184c4c4`（未コミットの現行文書変更を含む）
- 記号探索対象: `workers/src/*.py`、`tools/*.py`、`tools/*.ps1`、`typings/**/*.pyi`
- 設定・文書根拠: `workers/wrangler.jsonc`、`pyproject.toml`、`.github/workflows/*.yml`、`README.md`、`docs/*.md`
- 対象外: `.git/`、`.venv/`、`__pycache__/`、生成済み `artifacts/`。機密ファイル名に該当する `workers/service-account.json`、`.dev.vars*` は読み取っていない。

## 網羅性

| 区分 | 発見 | モデル化 | 詳細図掲載 | 除外 |
|---|---:|---:|---:|---:|
| ソースファイル | 20 | 20 | - | 0 |
| クラス | 8 | 8 | 8 | 0 |
| メソッド | 62 | 62 | 62 | 0 |
| 関数 | 261 | 261 | 261 | 0 |

## 図の使い分け

| 図 | 内容 |
|---|---|
| [`00-repository-overview.puml`](00-repository-overview.puml) | 起動元、Worker、同期・ジョブ、状態、外部 API の全体像と標準順序 |
| [`01-context-trust.puml`](01-context-trust.puml) | Cloudflare と外部サービス間の信頼境界 |
| [`02-api-dependencies.puml`](02-api-dependencies.puml) | 主要同期・認証・watch の API 入出力 |
| [`03-module-dependencies.puml`](03-module-dependencies.puml) | 通常 Worker 内の静的 import 依存 |
| [`apis/discord-sync.puml`](apis/discord-sync.puml) | 外部 API 入出力の詳細 |
| [`apis/google-apply.puml`](apis/google-apply.puml) | 外部 API 入出力の詳細 |
| [`apis/google-auth.puml`](apis/google-auth.puml) | 外部 API 入出力の詳細 |
| [`apis/google-calendar.puml`](apis/google-calendar.puml) | 外部 API 入出力の詳細 |
| [`apis/health-jobs.puml`](apis/health-jobs.puml) | 外部 API 入出力の詳細 |
| [`boundaries/cloudflare-state.puml`](boundaries/cloudflare-state.puml) | 状態と信頼境界の詳細 |
| [`classes/00-class-index.puml`](classes/00-class-index.puml) | クラス役割と依存の詳細 |
| [`classes/application.puml`](classes/application.puml) | クラス役割と依存の詳細 |
| [`classes/e2e.puml`](classes/e2e.puml) | クラス役割と依存の詳細 |
| [`classes/runtime-stubs.puml`](classes/runtime-stubs.puml) | クラス役割と依存の詳細 |
| [`classes/tooling.puml`](classes/tooling.puml) | クラス役割と依存の詳細 |
| [`flows/google-webhook.puml`](flows/google-webhook.puml) | 実行順序と分岐の詳細 |
| [`flows/scheduled.puml`](flows/scheduled.puml) | 実行順序と分岐の詳細 |
| [`flows/sync-dispatch.puml`](flows/sync-dispatch.puml) | 実行順序と分岐の詳細 |
| [`methods/00-method-index.puml`](methods/00-method-index.puml) | 全 62 メソッド・261 関数のモジュール別件数と分割先 |
| [`methods/discord-sync-01.puml`](methods/discord-sync-01.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/discord-sync-02.puml`](methods/discord-sync-02.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/discord-sync-03.puml`](methods/discord-sync-03.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/e2e-discord-01.puml`](methods/e2e-discord-01.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/e2e-discord-02.puml`](methods/e2e-discord-02.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/e2e-discord-03.puml`](methods/e2e-discord-03.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/e2e-entry-01.puml`](methods/e2e-entry-01.puml) | モジュール内シンボル詳細（7 件） |
| [`methods/e2e-entry-02.puml`](methods/e2e-entry-02.puml) | モジュール内シンボル詳細（6 件） |
| [`methods/e2e-google-01.puml`](methods/e2e-google-01.puml) | モジュール内シンボル詳細（7 件） |
| [`methods/e2e-google-02.puml`](methods/e2e-google-02.puml) | モジュール内シンボル詳細（7 件） |
| [`methods/e2e-notion-01.puml`](methods/e2e-notion-01.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/e2e-notion-02.puml`](methods/e2e-notion-02.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/e2e-notion-03.puml`](methods/e2e-notion-03.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/e2e-validator-01.puml`](methods/e2e-validator-01.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/e2e-validator-02.puml`](methods/e2e-validator-02.puml) | モジュール内シンボル詳細（5 件） |
| [`methods/entry-01.puml`](methods/entry-01.puml) | モジュール内シンボル詳細（11 件） |
| [`methods/entry-02.puml`](methods/entry-02.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/google-apply-01.puml`](methods/google-apply-01.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/google-apply-02.puml`](methods/google-apply-02.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/google-apply-03.puml`](methods/google-apply-03.puml) | モジュール内シンボル詳細（9 件） |
| [`methods/google-auth-01.puml`](methods/google-auth-01.puml) | モジュール内シンボル詳細（9 件） |
| [`methods/google-auth-02.puml`](methods/google-auth-02.puml) | モジュール内シンボル詳細（8 件） |
| [`methods/google-calendar.puml`](methods/google-calendar.puml) | モジュール内シンボル詳細（6 件） |
| [`methods/google-watch.puml`](methods/google-watch.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/health-checks.puml`](methods/health-checks.puml) | モジュール内シンボル詳細（6 件） |
| [`methods/jobs-01.puml`](methods/jobs-01.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/jobs-02.puml`](methods/jobs-02.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/runtime-stubs.puml`](methods/runtime-stubs.puml) | モジュール内シンボル詳細（4 件） |
| [`methods/state-01.puml`](methods/state-01.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/state-02.puml`](methods/state-02.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/state-03.puml`](methods/state-03.puml) | モジュール内シンボル詳細（12 件） |
| [`methods/sync-lock.puml`](methods/sync-lock.puml) | モジュール内シンボル詳細（5 件） |
| [`methods/utf8-tool.puml`](methods/utf8-tool.puml) | モジュール内シンボル詳細（4 件） |
| [`methods/validator-01.puml`](methods/validator-01.puml) | モジュール内シンボル詳細（10 件） |
| [`methods/validator-02.puml`](methods/validator-02.puml) | モジュール内シンボル詳細（9 件） |
| [`modules/e2e-dependencies.puml`](modules/e2e-dependencies.puml) | 隔離 E2E Worker と安全性検証ツールの静的依存 |
| [`modules/runtime-dependencies.puml`](modules/runtime-dependencies.puml) | 通常・E2E Worker から Cloudflare Workers 実行時型への依存 |

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
| P001 | component | `workers/src/entry.py` | HTTP、Cron、認可、同期とジョブの起動を一つの Worker エントリで調整する。 | workers/src/entry.py:1-693 | confirmed | 03-module-dependencies.puml、classes/application.puml、modules/runtime-dependencies.puml、modules/e2e-dependencies.puml、classes/e2e.puml、methods/00-method-index.puml、methods/entry-01.puml、methods/entry-02.puml |
| P002 | component | `workers/src/google_calendar_sync.py` | Google Calendar の差分イベントを取得し、次回カーソル候補を返す。 | workers/src/google_calendar_sync.py:1-187 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/google-calendar.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/google-calendar.puml |
| P003 | component | `workers/src/google_apply_sync.py` | Google Calendar の変更を Notion と Discord へ反映する。 | workers/src/google_apply_sync.py:1-884 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/google-apply.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/google-apply-01.puml、methods/google-apply-02.puml、methods/google-apply-03.puml |
| P004 | component | `workers/src/discord_notion_sync.py` | Discord Scheduled Events の差分を Notion と Google Calendar へ反映する。 | workers/src/discord_notion_sync.py:1-964 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/discord-sync.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/discord-sync-01.puml、methods/discord-sync-02.puml、methods/discord-sync-03.puml |
| P005 | component | `workers/src/google_auth.py` | Google API 用アクセストークンを設定、キャッシュ、ブローカー、サービスアカウントから解決する。 | workers/src/google_auth.py:1-516 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/discord-sync.puml、apis/google-auth.puml、apis/google-calendar.puml、modules/runtime-dependencies.puml、modules/e2e-dependencies.puml、methods/00-method-index.puml、methods/google-auth-01.puml、methods/google-auth-02.puml |
| P006 | component | `workers/src/google_watch.py` | Google Calendar watch を登録・更新し、有効期限内の状態を維持する。 | workers/src/google_watch.py:1-287 | confirmed | 02-api-dependencies.puml、03-module-dependencies.puml、apis/google-calendar.puml、flows/scheduled.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/google-watch.puml |
| P007 | component | `workers/src/health_checks.py` | Google、Notion、Discord への認証付き疎通結果をまとめる。 | workers/src/health_checks.py:1-160 | confirmed | 03-module-dependencies.puml、apis/health-jobs.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/health-checks.puml |
| P008 | component | `workers/src/jobs.py` | Q&A 通知、前日リマインド、終了済みページ整理の定期ジョブを実行する。 | workers/src/jobs.py:1-558 | confirmed | 00-repository-overview.puml、01-context-trust.puml、03-module-dependencies.puml、apis/health-jobs.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/jobs-01.puml、methods/jobs-02.puml |
| P009 | component | `workers/src/state.py` | Workers KV と Durable Object の状態アクセスを責務別に集約する。 | workers/src/state.py:1-349 | confirmed | 00-repository-overview.puml、01-context-trust.puml、03-module-dependencies.puml、boundaries/cloudflare-state.puml、classes/application.puml、modules/e2e-dependencies.puml、methods/00-method-index.puml、methods/state-01.puml、methods/state-02.puml、methods/state-03.puml |
| P010 | component | `workers/src/sync_lock_do.py` | 同期ロック、最終同期時刻、Webhook 重複判定、E2E manifest を Durable Object で直列化する。 | workers/src/sync_lock_do.py:1-228 | confirmed | 03-module-dependencies.puml、boundaries/cloudflare-state.puml、classes/application.puml、flows/sync-dispatch.puml、modules/runtime-dependencies.puml、modules/e2e-dependencies.puml、methods/00-method-index.puml、methods/sync-lock.puml |
| P011 | component | `tools/validate_plantuml.py` | モデル、カタログ、図、ローカル PlantUML 描画を一括検証する。 | tools/validate_plantuml.py:1-815 | confirmed | classes/tooling.puml、methods/00-method-index.puml、methods/validator-01.puml、methods/validator-02.puml |
| P012 | component | `tools/utf8-no-bom.ps1` | Git 対象ファイルの文字コードを検査し、指定時だけ UTF-8 BOM なしへ変換する。 | tools/utf8-no-bom.ps1:1-155 | confirmed | methods/00-method-index.puml、methods/utf8-tool.puml |
| P013 | component | `typings/workers/__init__.pyi` | Cloudflare Python Workers 実行時 API の静的型境界を宣言する。 | typings/workers/__init__.pyi:1-24 | confirmed | classes/runtime-stubs.puml、modules/runtime-dependencies.puml、modules/e2e-dependencies.puml、methods/00-method-index.puml、methods/runtime-stubs.puml |
| P014 | component | `外部同期サブシステム` | Google、Notion、Discord 間のイベント同期処理を論理的にまとめる。 | workers/src/google_calendar_sync.py:118-187、workers/src/google_apply_sync.py:599-884、workers/src/discord_notion_sync.py:808-960 | inferred | 00-repository-overview.puml、01-context-trust.puml、flows/google-webhook.puml、flows/scheduled.puml |
| P015 | component | `外部 API 群` | Google、Notion、Discord と任意の token broker を概要図上でまとめる。 | workers/src/google_calendar_sync.py:62-187、workers/src/google_apply_sync.py:194-596、workers/src/discord_notion_sync.py:213-805、workers/src/google_auth.py:124-450 | inferred | 00-repository-overview.puml、01-context-trust.puml |
| P016 | component | `Cloudflare 永続状態群` | STATE_KV と SyncCoordinator Durable Object storage を概要図上でまとめる。 | workers/wrangler.jsonc:9-23、workers/wrangler.jsonc:66-74、workers/src/state.py:1-349、workers/src/sync_lock_do.py:1-228 | inferred | 00-repository-overview.puml、01-context-trust.puml |
| P017 | component | `workers/src/e2e_discord_probe.py` | 隔離された E2E Worker から Discord の作成・検証・更新・削除と残存資源の回収を実行する。 | workers/src/e2e_discord_probe.py:1-1130 | confirmed | modules/e2e-dependencies.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/e2e-discord-01.puml、methods/e2e-discord-02.puml、methods/e2e-discord-03.puml |
| P018 | component | `workers/src/e2e_entry.py` | 通常 Worker の公開面を制限し、E2E 状態確認と明示的な CRUD プローブだけを提供する。 | workers/src/e2e_entry.py:1-376 | confirmed | modules/e2e-dependencies.puml、modules/runtime-dependencies.puml、classes/e2e.puml、methods/00-method-index.puml、methods/e2e-entry-01.puml、methods/e2e-entry-02.puml |
| P019 | component | `workers/src/e2e_google_probe.py` | 隔離された E2E Worker から Google Calendar の作成・検証・更新・削除と残存資源の回収を実行する。 | workers/src/e2e_google_probe.py:1-469 | confirmed | modules/e2e-dependencies.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/e2e-google-01.puml、methods/e2e-google-02.puml |
| P020 | component | `workers/src/e2e_notion_probe.py` | 隔離された E2E Worker から Notion の作成・検証・更新・アーカイブと残存資源の回収を実行する。 | workers/src/e2e_notion_probe.py:1-1185 | confirmed | modules/e2e-dependencies.puml、modules/runtime-dependencies.puml、methods/00-method-index.puml、methods/e2e-notion-01.puml、methods/e2e-notion-02.puml、methods/e2e-notion-03.puml |
| P021 | component | `tools/validate_e2e_mcp_config.py` | E2E 用 MCP 設定と承認モードが安全な制約を満たすか静的検証する。 | tools/validate_e2e_mcp_config.py:1-404 | confirmed | modules/e2e-dependencies.puml、methods/00-method-index.puml、methods/e2e-validator-01.puml |
| P022 | component | `tools/validate_e2e_secret_hygiene.py` | E2E 設定テンプレートと Git 追跡対象に秘密情報が混入していないか検証する。 | tools/validate_e2e_secret_hygiene.py:1-150 | confirmed | modules/e2e-dependencies.puml、methods/00-method-index.puml、methods/e2e-validator-01.puml |
| P023 | component | `tools/validate_e2e_workflow.py` | E2E GitHub Actions workflow の固定 action、手順、失敗条件を静的検証する。 | tools/validate_e2e_workflow.py:1-149 | confirmed | modules/e2e-dependencies.puml、methods/00-method-index.puml、methods/e2e-validator-02.puml |
| X001 | actor | `運用クライアント` | 認可付き管理・同期・ジョブルートを呼び出し、処理結果を受け取る。 | workers/src/entry.py:80-240 | inferred | 00-repository-overview.puml、01-context-trust.puml |
| T001 | interface | `Google Calendar push 通知` | 登録済み watch の channel token 付き変更通知を Webhook へ送り、検証後に差分同期を起動する。 | workers/src/google_watch.py:117-155、workers/src/entry.py:128-153 | confirmed | 00-repository-overview.puml、01-context-trust.puml、flows/google-webhook.puml |
| T002 | interface | `Cloudflare Cron Trigger` | Wrangler のスケジュールで scheduled ハンドラを起動する。 | workers/wrangler.jsonc:61-65、workers/src/entry.py:253-355 | confirmed | 00-repository-overview.puml、01-context-trust.puml、flows/scheduled.puml |
| A001 | api | `Worker HTTP API` | ヘルス、Webhook、同期、管理、ジョブの HTTP 要求をルーティングする。 | workers/src/entry.py:58-251 | confirmed | 00-repository-overview.puml、01-context-trust.puml、flows/google-webhook.puml、flows/sync-dispatch.puml |
| A002 | api | `Google Calendar API` | カレンダーイベント、watch、カレンダー情報の参照と更新を提供する。 | workers/src/google_calendar_sync.py:62-115、workers/src/google_watch.py:66-199、workers/src/discord_notion_sync.py:601-659 | confirmed | 02-api-dependencies.puml、apis/discord-sync.puml、apis/google-calendar.puml、apis/health-jobs.puml |
| A003 | api | `Google OAuth token endpoint` | サービスアカウント JWT assertion を検証し、アクセストークンを発行する。 | workers/src/google_auth.py:392-450 | confirmed | 02-api-dependencies.puml、apis/google-auth.puml |
| A004 | api | `設定可能な Google token broker` | 実行時 URL が設定された場合に Calendar スコープのトークンを返す。 | workers/src/google_auth.py:124-176 | runtime-unverified | 02-api-dependencies.puml、apis/google-auth.puml |
| A005 | api | `Notion API` | イベントと Q&A データベースの検索、ページ作成・更新・アーカイブを提供する。 | workers/src/google_apply_sync.py:194-408、workers/src/jobs.py:125-183 | confirmed | 02-api-dependencies.puml、apis/discord-sync.puml、apis/google-apply.puml、apis/health-jobs.puml |
| A006 | api | `Discord REST API` | Scheduled Events、メッセージ、リアクション、接続診断を提供する。 | workers/src/discord_notion_sync.py:213-304、workers/src/jobs.py:186-236 | confirmed | 02-api-dependencies.puml、apis/discord-sync.puml、apis/google-apply.puml、apis/health-jobs.puml |
| D001 | datastore | `Cloudflare Workers KV binding STATE_KV` | 同期カーソル、対応表、キュー、キャッシュ、watch 状態、診断結果を保持する。 | workers/wrangler.jsonc:66-74、workers/src/state.py:83-275 | confirmed | 02-api-dependencies.puml、apis/google-auth.puml、apis/health-jobs.puml、boundaries/cloudflare-state.puml |
| D002 | datastore | `Durable Object storage for SyncCoordinator/global` | 同期ロック、最終同期時刻、期限付き Webhook 重複キーを保持する。 | workers/wrangler.jsonc:9-23、workers/src/sync_lock_do.py:57-205 | confirmed | boundaries/cloudflare-state.puml |

## クラス（全件）

| ID | 完全名 | 役割 | 根拠 | 確実性 | 詳細図 |
|---|---|---|---|---|---|
| C001 | `entry.Default` | HTTP ルーティング、Cron 実行、認可、同期ディスパッチを Worker の入口として調整する。 | workers/src/entry.py:51-693 | confirmed | 00-repository-overview.puml、01-context-trust.puml、classes/00-class-index.puml、classes/application.puml、flows/google-webhook.puml、flows/scheduled.puml、flows/sync-dispatch.puml、classes/e2e.puml、methods/entry-01.puml、methods/entry-02.puml |
| C002 | `state.StateStore` | Workers KV への状態アクセスを集約し、整合性が必要な状態を Durable Object へ委譲する。 | workers/src/state.py:27-349 | confirmed | classes/00-class-index.puml、classes/application.puml、flows/google-webhook.puml、flows/scheduled.puml、flows/sync-dispatch.puml、methods/state-01.puml、methods/state-02.puml、methods/state-03.puml |
| C003 | `sync_lock_do.SyncCoordinator` | 同期ロック、最終同期時刻、Webhook 重複抑止を Durable Object 上で直列化する。 | workers/src/sync_lock_do.py:57-228 | confirmed | classes/00-class-index.puml、classes/application.puml、methods/sync-lock.puml |
| C004 | `tools.validate_plantuml.Findings` | PlantUML 成果物の検証中に見つかったエラーと警告を分類して保持する。 | tools/validate_plantuml.py:60-69 | confirmed | classes/00-class-index.puml、classes/tooling.puml、methods/validator-01.puml |
| C005 | `workers.Response` | Cloudflare Workers の HTTP 応答を表し、本文、状態、ヘッダを保持する。 | typings/workers/__init__.pyi:3-15 | runtime-unverified | classes/00-class-index.puml、classes/application.puml、classes/runtime-stubs.puml、methods/runtime-stubs.puml |
| C006 | `workers.WorkerEntrypoint` | Cloudflare Python Worker のエントリポイント基底型を表す。 | typings/workers/__init__.pyi:17-18 | runtime-unverified | classes/00-class-index.puml、classes/application.puml、classes/runtime-stubs.puml、classes/e2e.puml |
| C007 | `workers.DurableObject` | Cloudflare Durable Object の基底型と実行時状態を表す。 | typings/workers/__init__.pyi:20-22 | runtime-unverified | classes/00-class-index.puml、classes/application.puml、classes/runtime-stubs.puml |
| C008 | `e2e_entry.Default` | 通常 Worker を継承し、E2E 専用ルート、認可、排他、CRUD プローブ、Cron 無効化を調整する。 | workers/src/e2e_entry.py:197-376 | confirmed | classes/00-class-index.puml、classes/e2e.puml、methods/e2e-entry-02.puml |

## メソッド（全件）

| ID | 完全名 | 役割 | 入力 | 出力 | 副作用 | 根拠 | 詳細図 |
|---|---|---|---|---|---|---|---|
| M001 | `entry.Default.fetch` | HTTP エンドポイントを振り分ける。 | request | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/entry.py:59-252 | methods/entry-01.puml |
| M002 | `entry.Default.scheduled` | Cron Trigger 実行エントリ。 | controller、env、ctx | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/entry.py:254-356 | methods/entry-01.puml |
| M003 | `entry.Default._run_sync_dispatch` | 同期処理の中核ディスパッチ。 | request、state、source | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/entry.py:376-476 | methods/entry-01.puml |
| M004 | `sync_lock_do.SyncCoordinator.fetch` | POST body(JSON) の action に応じてロック操作を実行する。 | request | 処理結果 | 外部 HTTP 要求、状態またはファイルの更新、外部 API 呼び出し、永続状態の参照または更新 | workers/src/sync_lock_do.py:77-88 | methods/sync-lock.puml |
| M005 | `tools.validate_plantuml.Findings.__init__` | エラー一覧と警告一覧を空の状態で初期化する。 | なし | None | なし | tools/validate_plantuml.py:61-63 | methods/validator-01.puml |
| M006 | `tools.validate_plantuml.Findings.error` | 検証エラーをエラー一覧へ追加する。 | message | None | 検査結果の収集 | tools/validate_plantuml.py:65-66 | methods/validator-01.puml |
| M007 | `tools.validate_plantuml.Findings.warn` | 検証警告を警告一覧へ追加する。 | message | None | 検査結果の収集 | tools/validate_plantuml.py:68-69 | methods/validator-01.puml |
| M008 | `workers.Response.__init__` | HTTP 応答の本文、状態コード、ヘッダを初期化する。 | body、status、headers | None | なし | typings/workers/__init__.pyi:6-12 | methods/runtime-stubs.puml |
| M009 | `workers.Response.text` | HTTP 応答本文を文字列として返す実行時契約を表す。 | なし | str | なし | typings/workers/__init__.pyi:14-14 | methods/runtime-stubs.puml |
| M010 | `workers.Response.json` | HTTP 応答本文を JSON として返す実行時契約を表す。 | なし | Any | なし | typings/workers/__init__.pyi:15-15 | methods/runtime-stubs.puml |
| M011 | `entry.Default._authorized` | Bearer 認可判定。 | request | bool | なし | workers/src/entry.py:358-374 | methods/entry-01.puml |
| M012 | `entry.Default._sync_interval_seconds` | 同期クールダウン秒数を返す。 | なし | float | なし | workers/src/entry.py:478-484 | methods/entry-01.puml |
| M013 | `entry.Default._sync_all_mode` | 同期モード名を返す。 | なし | str | なし | workers/src/entry.py:486-488 | methods/entry-01.puml |
| M014 | `entry.Default._sync_all_include_discord_notion` | /sync/all で Discord->Notion を実行するか。 | なし | bool | なし | workers/src/entry.py:490-495 | methods/entry-01.puml |
| M015 | `entry.Default._durable_lock_enabled` | Durable Object ロック有効/無効。 | なし | bool | なし | workers/src/entry.py:497-499 | methods/entry-02.puml |
| M016 | `entry.Default._acquire_sync_lock` | SyncCoordinator Durable Object で排他ロックを取得する。 | source | 処理結果 | なし | workers/src/entry.py:501-548 | methods/entry-02.puml |
| M017 | `entry.Default._release_sync_lock` | 取得済みロックを解放する。 | owner | なし | なし | workers/src/entry.py:550-563 | methods/entry-02.puml |
| M018 | `entry.Default._sync_lock_ttl_seconds` | ロック TTL を秒で返す（最小 10 秒）。 | なし | float | なし | workers/src/entry.py:565-571 | methods/entry-02.puml |
| M019 | `entry.Default._migration_status` | 運用診断用ステータスを組み立てる。 | state、include_checks | dict | なし | workers/src/entry.py:573-636 | methods/entry-02.puml |
| M020 | `entry.Default._sync_lock_status` | Durable Object から現在のロック状態を取得する。 | なし | 処理結果 | なし | workers/src/entry.py:638-653 | methods/entry-02.puml |
| M021 | `entry.Default._get_sync_stub` | Durable Object namespace から "global" stub を取得(DO生成)する。 | do_ns | 処理結果 | なし | workers/src/entry.py:656-660 | methods/entry-02.puml |
| M022 | `entry.Default._do_stub_rpc` | SyncCoordinator RPC を JSON 文字列で呼び出し、追加の await 境界を吸収する。 | stub、payload | 処理結果 | 永続状態の参照または更新 | workers/src/entry.py:663-671 | methods/entry-02.puml |
| M023 | `entry.Default._to_bool_query` | クエリ文字列中の bool 値（1/true/yes/on）を判定する。 | query_string、key | bool | なし | workers/src/entry.py:682-693 | methods/entry-02.puml |
| M024 | `state.StateStore.__init__` | インスタンスが利用する依存状態を初期化する。 | env | なし | なし | workers/src/state.py:36-37 | methods/state-01.puml |
| M025 | `state.StateStore.enabled` | STATE_KV バインディングの有無を返す。 | なし | bool | なし | workers/src/state.py:39-41 | methods/state-01.puml |
| M026 | `state.StateStore._kv` | 内部ヘルパー: KV バインディングを返す。 | なし | 処理結果 | なし | workers/src/state.py:43-45 | methods/state-01.puml |
| M027 | `state.StateStore._sync_do` | 内部ヘルパー: SyncCoordinator Durable Object namespace を返す。 | なし | 処理結果 | なし | workers/src/state.py:47-49 | methods/state-01.puml |
| M028 | `state.StateStore._sync_do_stub` | Durable Object namespace から global stub を取得する。 | do_ns | 処理結果 | なし | workers/src/state.py:56-60 | methods/state-01.puml |
| M029 | `state.StateStore._sync_do_rpc` | SyncCoordinator RPC を JSON 文字列で呼び出して結果辞書へ復元する。 | stub、action、payload | 処理結果 | 永続状態の参照または更新 | workers/src/state.py:63-80 | methods/state-01.puml |
| M030 | `state.StateStore.get_text` | KV から文字列を取得し、空文字は None として扱う。 | key | str \| None | なし | workers/src/state.py:82-93 | methods/state-01.puml |
| M031 | `state.StateStore.put_text` | KV へ文字列を書き込む。 | key、value | なし | 状態またはファイルの更新、状態または外部資源の更新 | workers/src/state.py:95-100 | methods/state-01.puml |
| M032 | `state.StateStore.put_text_if_changed` | 現在値と異なる場合だけ KV へ文字列を書き込む。 | key、value | bool | 状態または外部資源の更新 | workers/src/state.py:102-109 | methods/state-01.puml |
| M033 | `state.StateStore.get_json` | KV の JSON 文字列を辞書等へ復元する。 | key、default | 処理結果 | なし | workers/src/state.py:111-119 | methods/state-02.puml |
| M034 | `state.StateStore.put_json` | Python オブジェクトを JSON 化して KV へ保存する。 | key、payload | なし | 状態または外部資源の更新 | workers/src/state.py:121-126 | methods/state-02.puml |
| M035 | `state.StateStore.put_json_if_changed` | 現在値と異なる場合だけ JSON を KV へ保存する。 | key、payload | bool | 状態または外部資源の更新 | workers/src/state.py:128-135 | methods/state-02.puml |
| M036 | `state.StateStore.mark_google_message_seen` | Google webhook 重複通知抑止用。 | channel_id、message_number | bool | なし | workers/src/state.py:182-211 | methods/state-02.puml |
| M037 | `state.StateStore.get_sync_updated_min` | Google差分同期カーソル(updatedMin)を取得する。 | なし | str \| None | 状態または外部資源の更新 | workers/src/state.py:213-215 | methods/state-02.puml |
| M038 | `state.StateStore.set_sync_updated_min` | Google差分同期カーソル(updatedMin)を保存する。 | updated_min | なし | 状態または外部資源の更新 | workers/src/state.py:217-220 | methods/state-02.puml |
| M039 | `state.StateStore.get_sync_last_epoch` | 最後に同期成功した時刻(epoch秒)を取得する。 | なし | float | なし | workers/src/state.py:222-238 | methods/state-02.puml |
| M040 | `state.StateStore.set_sync_last_epoch_now` | 最後の同期時刻を現在時刻で更新する。 | なし | なし | 状態または外部資源の更新 | workers/src/state.py:240-248 | methods/state-02.puml |
| M041 | `state.StateStore.should_skip_sync_by_cooldown` | クールダウン判定。 | interval_seconds | bool | なし | workers/src/state.py:250-256 | methods/state-02.puml |
| M042 | `state.StateStore.get_gcal_discord_map` | GoogleイベントID -> DiscordイベントID の対応表を取得する。 | なし | dict | なし | workers/src/state.py:258-261 | methods/state-03.puml |
| M043 | `state.StateStore.set_gcal_discord_map` | GoogleイベントID -> DiscordイベントID の対応表を保存する。 | data | なし | 状態または外部資源の更新 | workers/src/state.py:263-265 | methods/state-03.puml |
| M044 | `state.StateStore.get_gcal_notion_map` | GoogleイベントID -> NotionページID の対応表を取得する。 | なし | dict | なし | workers/src/state.py:267-274 | methods/state-03.puml |
| M045 | `state.StateStore.set_gcal_notion_map` | GoogleイベントID -> NotionページID の対応表を保存する。 | data | なし | 状態または外部資源の更新 | workers/src/state.py:276-281 | methods/state-03.puml |
| M046 | `state.StateStore.get_discord_snapshot` | Discordポーリング差分検知用スナップショットを取得する。 | なし | dict | なし | workers/src/state.py:283-286 | methods/state-03.puml |
| M047 | `state.StateStore.set_discord_snapshot` | Discordポーリング差分検知用スナップショットを保存する。 | data | なし | 状態または外部資源の更新 | workers/src/state.py:288-290 | methods/state-03.puml |
| M048 | `state.StateStore.set_last_result` | ジョブ/同期結果を `result:<op_name>` に保存する。 | op_name、payload | なし | 状態または外部資源の更新 | workers/src/state.py:292-314 | methods/state-03.puml |
| M049 | `state.StateStore.get_last_result` | `result:<op_name>` の最新結果を取得する。 | op_name | 処理結果 | なし | workers/src/state.py:316-321 | methods/state-03.puml |
| M050 | `state.StateStore.result_write_min_interval_seconds` | 同一内容の last_result を再保存する最小間隔を返す。 | env | float | なし | workers/src/state.py:324-330 | methods/state-03.puml |
| M051 | `state.StateStore.google_message_dedupe_ttl_seconds` | Google webhook 重複抑止の保持秒数を返す。 | env | float | なし | workers/src/state.py:333-339 | methods/state-03.puml |
| M052 | `state.StateStore.is_kv_sync_cooldown_enabled` | 同期クールダウン機能の有効/無効を返す。 | env | bool | なし | workers/src/state.py:342-344 | methods/state-03.puml |
| M053 | `state.StateStore.is_gcal_dedupe_enabled` | Google webhook 重複抑止機能の有効/無効を返す。 | env | bool | なし | workers/src/state.py:347-349 | methods/state-03.puml |
| M054 | `entry.Default._decode_do_rpc` | SyncCoordinator RPC の JSON 文字列を結果辞書へ復元する。 | value | dict | なし | workers/src/entry.py:674-679 | methods/entry-02.puml |
| M055 | `state.StateStore.e2e_manifest_enabled` | E2E cleanup 所有権用 Durable Object binding の有無を返す。 | なし | bool | なし | workers/src/state.py:51-53 | methods/state-01.puml |
| M056 | `state.StateStore.get_e2e_manifest` | E2E cleanup manifest を強整合な Durable Object から取得する。 | service | dict \| None | 永続状態の参照または更新 | workers/src/state.py:137-154 | methods/state-02.puml |
| M057 | `state.StateStore.put_e2e_manifest` | E2E cleanup manifest を強整合な Durable Object へ保存する。 | service、payload | None | 永続状態の参照または更新 | workers/src/state.py:156-172 | methods/state-02.puml |
| M058 | `state.StateStore.get_legacy_e2e_manifest` | 旧 KV manifest を移行判定専用に読み、所有権の正本と分離する。 | service | dict \| None | 永続状態の参照または更新 | workers/src/state.py:174-180 | methods/state-02.puml |
| M059 | `sync_lock_do.SyncCoordinator.sync_state` | 同期状態操作を RPC で受け取り、結果を JSON 文字列で返す。 | payload_json | str | 永続状態の参照または更新 | workers/src/sync_lock_do.py:68-75 | methods/sync-lock.puml |
| M060 | `sync_lock_do.SyncCoordinator._handle_action` | 同期ロック、重複抑止、E2E manifest の action を直列化して処理する。 | payload | tuple[dict, int] | 永続状態の参照または更新 | workers/src/sync_lock_do.py:90-228 | methods/sync-lock.puml |
| M061 | `e2e_entry.Default.fetch` | E2E 専用状態・CRUD ルートを認可し、通常 Worker の許可済みルートへ委譲する。 | request | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_entry.py:200-372 | methods/e2e-entry-02.puml |
| M062 | `e2e_entry.Default.scheduled` | E2E Worker の Cron 実行を常に無効化する。 | controller、env、ctx | 処理結果 | なし | workers/src/e2e_entry.py:374-376 | methods/e2e-entry-02.puml |

## 関数（全件）

| ID | 完全名 | 役割 | 入力 | 出力 | 副作用 | 根拠 | 詳細図 |
|---|---|---|---|---|---|---|---|
| F001 | `google_calendar_sync.run_google_delta_fetch` | Googleカレンダーの差分イベントを取る KV の同期カーソル(updated_min)を更新する ただし Notion/Discord への同期はまだやらない | env、state、commit_cursor | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/google_calendar_sync.py:118-187 | flows/sync-dispatch.puml、methods/google-calendar.puml |
| F002 | `google_apply_sync.apply_google_events` | Google Calendar のイベント一覧を受け取り、Notion と Discord に反映する。 | env、state、events | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/google_apply_sync.py:599-884 | flows/sync-dispatch.puml、methods/google-apply-03.puml |
| F003 | `discord_notion_sync.run_discord_notion_poll_sync` | 定期ポーリングのメイン処理。 | env、state | 処理結果 | 標準出力、外部 API 呼び出し、永続状態の参照または更新 | workers/src/discord_notion_sync.py:812-964 | flows/sync-dispatch.puml、methods/discord-sync-03.puml |
| F004 | `google_auth.get_google_access_token` | 設定、KV キャッシュ、token broker、サービスアカウントの順に Google API token を解決する。 | env、state | 処理結果 | なし | workers/src/google_auth.py:460-482 | methods/google-auth-02.puml |
| F005 | `google_watch.ensure_watch_active` | watch が有効な状態を保つ。 | env、state | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/google_watch.py:230-287 | methods/google-watch.puml |
| F006 | `health_checks.run_connectivity_checks` | 3サービスの疎通確認をまとめて実行し、結果を返す。 | env、state | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/health_checks.py:149-160 | methods/health-checks.puml |
| F007 | `jobs.run_qa_notification_job` | QA通知ジョブ本体。 | env、state、return_detail | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/jobs.py:272-342 | flows/scheduled.puml、methods/jobs-02.puml |
| F008 | `jobs.run_day_before_reminder_job` | 前日リマインド。 | env、state、return_detail | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/jobs.py:369-460 | flows/scheduled.puml、methods/jobs-02.puml |
| F009 | `jobs.run_auto_clean_job` | Notion cleanup ジョブ本体。 | env、state、return_detail | 処理結果 | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/jobs.py:509-558 | flows/scheduled.puml、methods/jobs-02.puml |
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
| F055 | `discord_notion_sync._notify_discord_event_created` | 新規作成された Discord イベントを通知チャンネルへ投稿する。 | env、event | bool | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:534-559 | methods/discord-sync-03.puml |
| F056 | `discord_notion_sync._google_sync_enabled` | Discord -> Google 同期を有効化する条件判定。 | env | bool | なし | workers/src/discord_notion_sync.py:562-572 | methods/discord-sync-03.puml |
| F057 | `discord_notion_sync._google_event_body` | Discordイベント情報を Google Calendar events API 用のボディに変換する。 | name、description、start_dt、end_dt、location、discord_event_id | 処理結果 | なし | workers/src/discord_notion_sync.py:575-602 | methods/discord-sync-03.puml |
| F058 | `discord_notion_sync._google_create_event` | Google Calendar にイベントを新規作成する。 | env、token、payload | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:605-625 | methods/discord-sync-03.puml |
| F059 | `discord_notion_sync._google_update_event` | Google Calendar イベントを PATCH 更新する。 | env、token、google_event_id、payload | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:628-646 | methods/discord-sync-03.puml |
| F060 | `discord_notion_sync._google_delete_event` | Google Calendar イベントを削除する。 | env、token、google_event_id | 処理結果 | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/discord_notion_sync.py:649-663 | methods/discord-sync-03.puml |
| F061 | `discord_notion_sync._sync_discord_event_upsert` | Discordの単一イベントを Notion/Google に同期する。 | env、event、google_token | bool | なし | workers/src/discord_notion_sync.py:666-783 | methods/discord-sync-03.puml |
| F062 | `discord_notion_sync._sync_discord_event_delete` | Discord から削除されたイベントを Google/Notion から除去する。 | env、event_id、google_token | bool | 状態または外部資源の更新 | workers/src/discord_notion_sync.py:786-809 | methods/discord-sync-03.puml |
| F063 | `entry._json_response` | JSON レスポンスを統一フォーマットで返す。 | payload、status | Response | なし | workers/src/entry.py:22-28 | methods/entry-01.puml |
| F064 | `entry._header` | HTTP ヘッダ値を trim して取得する。 | request、name | str \| None | なし | workers/src/entry.py:31-37 | methods/entry-01.puml |
| F065 | `entry._bool_env` | 環境変数文字列を bool として解釈する。 | value、default | bool | なし | workers/src/entry.py:40-44 | methods/entry-01.puml |
| F066 | `entry._detail_dict` | 任意の処理結果を詳細応答用の辞書へ正規化する。 | value | dict[str, Any] | なし | workers/src/entry.py:47-48 | methods/entry-01.puml |
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
| F096 | `google_auth._b64url` | JWT 用 Base64URL エンコード（パディングなし）。 | data | str | なし | workers/src/google_auth.py:45-49 | methods/google-auth-01.puml |
| F097 | `google_auth._env_text` | Worker env から文字列を安全に取得する。 | env、key、default | str | なし | workers/src/google_auth.py:52-60 | methods/google-auth-01.puml |
| F098 | `google_auth._get_cached_token` | KV からキャッシュ済みGoogleアクセストークンを取得する。 | state | 処理結果 | なし | workers/src/google_auth.py:63-81 | methods/google-auth-01.puml |
| F099 | `google_auth._get_cached_token_meta` | キャッシュトークンの存在・有効性メタ情報(健康度)を返す。 | state | 処理結果 | なし | workers/src/google_auth.py:84-117 | methods/google-auth-01.puml |
| F100 | `google_auth._save_cached_token` | token と任意の有効期限(epoch)を KV に保存する。 | state、token、expires_at | なし | 状態または外部資源の更新 | workers/src/google_auth.py:120-128 | methods/google-auth-01.puml |
| F101 | `google_auth._fetch_token_from_broker` | 外部トークンブローカーからトークンを取得する。 | env、state | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_auth.py:131-183 | methods/google-auth-01.puml |
| F102 | `google_auth._load_service_account_info_from_env` | Service Account JSON を env から読み込む。 | env | 処理結果 | なし | workers/src/google_auth.py:186-205 | methods/google-auth-01.puml |
| F103 | `google_auth._pem_pkcs8_to_der` | PEM 形式の PKCS8 秘密鍵を DER(bytes) に変換する。 | private_key_pem | 処理結果 | なし | workers/src/google_auth.py:208-222 | methods/google-auth-01.puml |
| F104 | `google_auth._js_uint8_array` | Python bytes を JS Uint8Array に変換する。 | data | 処理結果 | なし | workers/src/google_auth.py:225-236 | methods/google-auth-02.puml |
| F105 | `google_auth._uint8_array_to_bytes` | JS Uint8Array を Python bytes に変換する。 | js_arr | 処理結果 | なし | workers/src/google_auth.py:239-256 | methods/google-auth-02.puml |
| F106 | `google_auth._sign_rs256` | Web Crypto API を使い、JWT 署名対象を RS256 で署名する。 | message、private_key_pem | 処理結果 | なし | workers/src/google_auth.py:259-330 | methods/google-auth-02.puml |
| F107 | `google_auth._build_service_account_assertion` | OAuth JWT Bearer 用 JWT を生成する。 | sa_info、scope | 処理結果 | なし | workers/src/google_auth.py:333-396 | methods/google-auth-02.puml |
| F108 | `google_auth._fetch_token_from_service_account` | JWT アサーションを使ってGoogle の OAuth トークンエンドポイントから アクセストークンを取得する。 | env、state | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_auth.py:399-457 | methods/google-auth-02.puml |
| F109 | `google_auth.describe_google_auth_sources` | 現在利用可能な認証ソース状態を返す。 | env、state | 処理結果 | なし | workers/src/google_auth.py:485-499 | methods/google-auth-02.puml |
| F110 | `google_auth.set_google_access_token` | 管理API経由で受け取ったトークンを KV キャッシュに保存する。 | state、access_token、expires_in_seconds | 処理結果 | 状態または外部資源の更新 | workers/src/google_auth.py:502-516 | methods/google-auth-02.puml |
| F111 | `google_calendar_sync.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/google_calendar_sync.py:11-21 | methods/google-calendar.puml |
| F112 | `google_calendar_sync._env_text` | Worker env から文字列を安全に取得する。 | env、key、default | str | なし | workers/src/google_calendar_sync.py:31-39 | methods/google-calendar.puml |
| F113 | `google_calendar_sync._parse_rfc3339` | RFC3339 文字列を datetime へ変換する。 | value | 処理結果 | なし | workers/src/google_calendar_sync.py:42-52 | methods/google-calendar.puml |
| F114 | `google_calendar_sync._to_rfc3339_z` | 日時を UTC の RFC3339 Z 形式へ変換する。 | dt | str | なし | workers/src/google_calendar_sync.py:55-59 | methods/google-calendar.puml |
| F115 | `google_calendar_sync._google_events_list` | Google Calendar events.list をページングし、イベント一覧を取得する。 | calendar_id、bearer_token、updated_min | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_calendar_sync.py:62-115 | methods/google-calendar.puml |
| F116 | `google_watch.fetch` | Cloudflare Workers の外部 HTTP fetch 実装へ要求を委譲して応答を返す。 | url、options | Any | 外部 HTTP 要求、外部プロセスまたは HTTP 要求 | workers/src/google_watch.py:17-27 | methods/google-watch.puml |
| F117 | `google_watch._env_text` | Worker env から文字列設定を取得する。 | env、key、default | str | なし | workers/src/google_watch.py:30-41 | methods/google-watch.puml |
| F118 | `google_watch._watch_call` | Google Calendar watch API を呼び、外部エラー本文を除いた結果を返す共通ラッパー。 | env、state、method、path、payload | 処理結果 | 外部プロセスまたは HTTP 要求 | workers/src/google_watch.py:66-114 | methods/google-watch.puml |
| F119 | `google_watch.register_watch` | channel token 付き Google Calendar events.watch を登録し、watch 状態を保存する。 | env、state | 処理結果 | 外部 API 呼び出し、永続状態の更新 | workers/src/google_watch.py:117-155 | methods/google-watch.puml |
| F120 | `google_watch.renew_watch` | 既存 watch を停止し、現在の channel token で新しい watch を登録する。 | env、state | 処理結果 | 外部 API 呼び出し、永続状態の更新 | workers/src/google_watch.py:158-199 | methods/google-watch.puml |
| F121 | `google_watch._parse_expiration_epoch_seconds` | Google watch expiration (ミリ秒)を桁を見て秒に変換する。 | expiration_value | float | なし | workers/src/google_watch.py:202-215 | methods/google-watch.puml |
| F122 | `google_watch._renew_threshold_seconds` | watch 更新しきい値（秒）を返す。 | env | float | なし | workers/src/google_watch.py:218-227 | methods/google-watch.puml |
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
| F145 | `jobs._utc_now` | UTC 現在時刻を返す。 | なし | 処理結果 | なし | workers/src/jobs.py:463-465 | methods/jobs-02.puml |
| F146 | `jobs._notion_archive_page` | Notion ページを archived=true に更新する。 | env、page_id | bool | 外部プロセスまたは HTTP 要求、状態または外部資源の更新 | workers/src/jobs.py:468-482 | methods/jobs-02.puml |
| F147 | `jobs._archive_internal_due` | 内部DBのアーカイブ判定。 | date_obj、now_utc | bool | 状態または外部資源の更新 | workers/src/jobs.py:485-497 | methods/jobs-02.puml |
| F148 | `jobs._cleanup_interval_seconds` | cleanup ジョブ最小実行間隔（秒）を返す。 | env | int | なし | workers/src/jobs.py:500-506 | methods/jobs-02.puml |
| F149 | `state._bool_env` | 環境変数文字列を bool として解釈する。 | value、default | bool | なし | workers/src/state.py:15-19 | methods/state-01.puml |
| F150 | `state._json_text` | JSON 比較/保存用に安定した文字列表現へ正規化する。 | payload | str | なし | workers/src/state.py:22-24 | methods/state-01.puml |
| F151 | `sync_lock_do._decode_lock_record` | storage.get("lock") の返り値を lock 辞書へ正規化する。 | value | dict | なし | workers/src/sync_lock_do.py:18-40 | methods/sync-lock.puml |
| F152 | `sync_lock_do._decode_json_record` | storage 上の JSON 文字列/辞書を dict に正規化する。 | value | dict | なし | workers/src/sync_lock_do.py:43-54 | methods/sync-lock.puml |
| F153 | `google_watch._channel_token` | Worker env から Google Webhook の channel token を取得する。 | env | str | なし | workers/src/google_watch.py:44-45 | methods/google-watch.puml |
| F154 | `google_watch._channel_token_error` | channel token の必須条件と256文字上限を検証し、エラーコードまたは None を返す。 | token | str \| None | なし | workers/src/google_watch.py:48-53 | methods/google-watch.puml |
| F155 | `google_watch._token_fingerprint` | channel token を保存用の SHA-256 fingerprint へ変換する。 | token | str | なし | workers/src/google_watch.py:56-57 | methods/google-watch.puml |
| F156 | `google_watch._watch_state_for_response` | watch 状態を複製し、token fingerprint を除いた応答用辞書を返す。 | watch_state | dict[str, Any] | なし | workers/src/google_watch.py:60-63 | methods/google-watch.puml |
| F157 | `e2e_discord_probe.fetch` | Workers fetch の呼び出し形式差を吸収する。 | url、options | Any | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:21-32 | methods/e2e-discord-01.puml |
| F158 | `e2e_discord_probe._env_text` | Worker 環境変数から文字列設定を安全に取得する。 | env、key | str | なし | workers/src/e2e_discord_probe.py:35-39 | methods/e2e-discord-01.puml |
| F159 | `e2e_discord_probe._fingerprint` | 正規化した検証対象を SHA-256 指紋へ変換する。 | value | str | なし | workers/src/e2e_discord_probe.py:42-43 | methods/e2e-discord-01.puml |
| F160 | `e2e_discord_probe._int_value` | 入力値を既定値付きの整数へ変換する。 | value、default | int | なし | workers/src/e2e_discord_probe.py:46-50 | methods/e2e-discord-01.puml |
| F161 | `e2e_discord_probe._new_run_id` | 時刻と乱数から形式制約を満たす E2E run ID を生成する。 | なし | str | なし | workers/src/e2e_discord_probe.py:53-55 | methods/e2e-discord-01.puml |
| F162 | `e2e_discord_probe._run_marker` | E2E 資源を run ID に結び付ける識別マーカーを生成する。 | run_id | str | なし | workers/src/e2e_discord_probe.py:58-59 | methods/e2e-discord-01.puml |
| F163 | `e2e_discord_probe._discord_iso` | 日時を Discord API 用 ISO 8601 文字列へ変換する。 | value | str | なし | workers/src/e2e_discord_probe.py:62-71 | methods/e2e-discord-01.puml |
| F164 | `e2e_discord_probe._event_payload` | Discord Scheduled Event の作成 payload を組み立てる。 | run_id | dict | なし | workers/src/e2e_discord_probe.py:74-89 | methods/e2e-discord-01.puml |
| F165 | `e2e_discord_probe._event_update_payload` | Discord Scheduled Event の更新 payload を組み立てる。 | run_id | dict | なし | workers/src/e2e_discord_probe.py:92-100 | methods/e2e-discord-01.puml |
| F166 | `e2e_discord_probe._allowed_mentions` | Discord 通知で許可する role mention を最小範囲で組み立てる。 | role_id | dict | なし | workers/src/e2e_discord_probe.py:103-108 | methods/e2e-discord-01.puml |
| F167 | `e2e_discord_probe._message_payload` | Discord 検証メッセージの payload を組み立てる。 | run_id、role_id、updated | dict | なし | workers/src/e2e_discord_probe.py:111-116 | methods/e2e-discord-02.puml |
| F168 | `e2e_discord_probe._header_text` | HTTP 応答ヘッダから正規化済み文字列を取得する。 | response、name | str | なし | workers/src/e2e_discord_probe.py:119-127 | methods/e2e-discord-02.puml |
| F169 | `e2e_discord_probe._retry_after_seconds` | rate limit 応答から再試行待機秒数を安全に算出する。 | response、data | float \| None | なし | workers/src/e2e_discord_probe.py:130-137 | methods/e2e-discord-02.puml |
| F170 | `e2e_discord_probe._discord_request` | 小さなDiscord JSON APIを呼び、429だけ公式待機値で再試行する。 | env、method、path、payload | tuple[int, Any, int] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:140-188 | methods/e2e-discord-02.puml |
| F171 | `e2e_discord_probe._request_stage` | E2E API 操作を実行し、manifest の段階状態と失敗情報を更新する。 | env、stages、retries、key、method、path、payload | tuple[int, Any] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:191-209 | methods/e2e-discord-02.puml |
| F172 | `e2e_discord_probe._event_matches` | 取得したイベントが E2E の期待値と一致するか判定する。 | event、event_id、guild_id、run_id、name、location | bool | なし | workers/src/e2e_discord_probe.py:212-232 | methods/e2e-discord-02.puml |
| F173 | `e2e_discord_probe._event_has_run` | イベントが指定 run ID の所有資源か判定する。 | event、event_id、guild_id、run_id | bool | なし | workers/src/e2e_discord_probe.py:235-246 | methods/e2e-discord-02.puml |
| F174 | `e2e_discord_probe._message_matches` | 取得した Discord メッセージが E2E の期待値と一致するか判定する。 | message、message_id、channel_id、role_id、run_id、content | bool | なし | workers/src/e2e_discord_probe.py:249-268 | methods/e2e-discord-02.puml |
| F175 | `e2e_discord_probe._message_has_run` | Discord メッセージが指定 run ID の所有資源か判定する。 | message、message_id、channel_id、run_id | bool | なし | workers/src/e2e_discord_probe.py:271-282 | methods/e2e-discord-02.puml |
| F176 | `e2e_discord_probe._has_own_check_reaction` | 検証メッセージに Bot 自身の確認リアクションがあるか判定する。 | message | bool | なし | workers/src/e2e_discord_probe.py:285-297 | methods/e2e-discord-02.puml |
| F177 | `e2e_discord_probe._verify_targets` | E2E 操作対象の設定値を検査し、安全な識別指紋を返す。 | env、stages、retries | str | なし | workers/src/e2e_discord_probe.py:300-362 | methods/e2e-discord-03.puml |
| F178 | `e2e_discord_probe._find_event_by_run` | 指定 run ID の E2E イベントを検索する。 | env、guild_id、run_id、stages、retries、stage_key | tuple[str, str] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:365-397 | methods/e2e-discord-03.puml |
| F179 | `e2e_discord_probe._find_message_by_run` | 指定 run ID の E2E メッセージを検索する。 | env、channel_id、run_id、stages、retries、stage_key | tuple[str, str] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:400-432 | methods/e2e-discord-03.puml |
| F180 | `e2e_discord_probe._delete_event` | E2E が所有する外部イベントを削除する。 | env、guild_id、event_id、run_id、stages、retries | tuple[bool, int, int, str] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:435-480 | methods/e2e-discord-03.puml |
| F181 | `e2e_discord_probe._delete_message` | E2E が所有する Discord メッセージを削除する。 | env、channel_id、message_id、run_id、stages、retries | tuple[bool, int, int, str] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:483-527 | methods/e2e-discord-03.puml |
| F182 | `e2e_discord_probe._cleanup_resources` | manifest に記録された E2E 所有資源を回収する。 | env、manifest | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:530-607 | methods/e2e-discord-03.puml |
| F183 | `e2e_discord_probe._target_fingerprints` | 操作対象 ID を値そのものではなく SHA-256 指紋へ変換する。 | guild_id、channel_id | dict[str, str] | なし | workers/src/e2e_discord_probe.py:610-614 | methods/e2e-discord-03.puml |
| F184 | `e2e_discord_probe._clean_manifest` | 成功済み E2E manifest を秘密値のない確定状態へ整形する。 | run_id、outcome、cleanup_attempts、stages、guild_id、channel_id、role_id、event_id、message_id、started_at | dict | なし | workers/src/e2e_discord_probe.py:617-649 | methods/e2e-discord-03.puml |
| F185 | `e2e_discord_probe.run_discord_crud_probe` | Discord の作成・検証・更新・削除を実行し、cleanup manifest と段階結果を返す。 | env、state、run_id | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:652-1032 | methods/e2e-discord-03.puml |
| F186 | `e2e_discord_probe.cleanup_discord_crud_probe` | Discord E2E manifest が所有する残存イベントとメッセージを回収する。 | env、state、expected_run_id | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_discord_probe.py:1035-1130 | methods/e2e-discord-03.puml |
| F187 | `e2e_entry._request_run_id` | 要求ヘッダから形式検証済みの E2E run ID を取得する。 | request | str | なし | workers/src/e2e_entry.py:63-66 | methods/e2e-entry-01.puml |
| F188 | `e2e_entry._manifest_summary` | E2E manifest を秘密値を含まない状態応答へ要約する。 | value | dict | なし | workers/src/e2e_entry.py:69-100 | methods/e2e-entry-01.puml |
| F189 | `e2e_entry._binding_value` | Workers binding の辞書・属性形式を安全な文字列へ正規化する。 | binding、key | str | なし | workers/src/e2e_entry.py:103-110 | methods/e2e-entry-01.puml |
| F190 | `e2e_entry._worker_version_summary` | Worker version metadata を存在状態と SHA-256 指紋へ要約する。 | env | dict | なし | workers/src/e2e_entry.py:113-123 | methods/e2e-entry-01.puml |
| F191 | `e2e_entry._watch_summary` | Google watch 状態を値そのものではなく SHA-256 指紋へ要約する。 | value | dict | なし | workers/src/e2e_entry.py:126-134 | methods/e2e-entry-01.puml |
| F192 | `e2e_entry._required_env_summary` | E2E 公開操作に必要な環境設定の有無だけを要約する。 | env | dict[str, bool] | なし | workers/src/e2e_entry.py:137-155 | methods/e2e-entry-01.puml |
| F193 | `e2e_entry._google_auth_summary` | Google 認証源の設定・キャッシュ状態を真偽値だけで要約する。 | value | dict[str, bool] | なし | workers/src/e2e_entry.py:158-170 | methods/e2e-entry-01.puml |
| F194 | `e2e_entry._sync_lock_summary` | SyncCoordinator の利用可否と応答状態を要約する。 | value | dict | なし | workers/src/e2e_entry.py:173-179 | methods/e2e-entry-02.puml |
| F195 | `e2e_entry._e2e_google_crud_enabled` | Google Calendar E2E CRUD ルートの有効設定を判定する。 | env | bool | なし | workers/src/e2e_entry.py:182-184 | methods/e2e-entry-02.puml |
| F196 | `e2e_entry._e2e_discord_crud_enabled` | Discord E2E CRUD ルートの有効設定を判定する。 | env | bool | なし | workers/src/e2e_entry.py:187-189 | methods/e2e-entry-02.puml |
| F197 | `e2e_entry._e2e_notion_crud_enabled` | Notion E2E CRUD ルートの有効設定を判定する。 | env | bool | なし | workers/src/e2e_entry.py:192-194 | methods/e2e-entry-02.puml |
| F198 | `e2e_google_probe.fetch` | Workers fetch の呼び出し形式差を吸収する。 | url、options | Any | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_google_probe.py:20-31 | methods/e2e-google-01.puml |
| F199 | `e2e_google_probe._env_text` | Worker 環境変数から文字列設定を安全に取得する。 | env、key | str | なし | workers/src/e2e_google_probe.py:34-38 | methods/e2e-google-01.puml |
| F200 | `e2e_google_probe._event_collection_url` | Google Calendar event 一覧 API の URL を組み立てる。 | calendar_id | str | なし | workers/src/e2e_google_probe.py:41-42 | methods/e2e-google-01.puml |
| F201 | `e2e_google_probe._event_item_url` | Google Calendar event 単体 API の URL を組み立てる。 | calendar_id、event_id | str | なし | workers/src/e2e_google_probe.py:45-49 | methods/e2e-google-01.puml |
| F202 | `e2e_google_probe._calendar_fingerprint` | Google Calendar ID を SHA-256 指紋へ変換する。 | calendar_id | str | なし | workers/src/e2e_google_probe.py:52-53 | methods/e2e-google-01.puml |
| F203 | `e2e_google_probe._google_request` | Google Calendar APIへ小さなJSONリクエストを送り、statusと辞書を返す。 | method、url、bearer_token、payload | tuple[int, dict] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_google_probe.py:56-86 | methods/e2e-google-01.puml |
| F204 | `e2e_google_probe._event_payload` | Google Calendar event の作成・更新 payload を組み立てる。 | run_id、event_id | dict | なし | workers/src/e2e_google_probe.py:89-116 | methods/e2e-google-01.puml |
| F205 | `e2e_google_probe._new_run_id` | 時刻と乱数から形式制約を満たす E2E run ID を生成する。 | なし | str | なし | workers/src/e2e_google_probe.py:119-121 | methods/e2e-google-02.puml |
| F206 | `e2e_google_probe._event_has_run` | イベントが指定 run ID の所有資源か判定する。 | event、event_id、run_id | bool | なし | workers/src/e2e_google_probe.py:124-129 | methods/e2e-google-02.puml |
| F207 | `e2e_google_probe._event_matches` | 取得したイベントが E2E の期待値と一致するか判定する。 | event、event_id、run_id、summary | bool | なし | workers/src/e2e_google_probe.py:132-136 | methods/e2e-google-02.puml |
| F208 | `e2e_google_probe._delete_event` | run ID一致を確認してから削除し、失敗時は3回まで再試行する。 | calendar_id、event_id、run_id、bearer_token | tuple[bool, int, int, str] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_google_probe.py:139-164 | methods/e2e-google-02.puml |
| F209 | `e2e_google_probe._clean_manifest` | 成功済み E2E manifest を秘密値のない確定状態へ整形する。 | run_id、outcome、cleanup_attempts、stages、calendar_id、event_id、started_at | dict | なし | workers/src/e2e_google_probe.py:167-191 | methods/e2e-google-02.puml |
| F210 | `e2e_google_probe.run_google_calendar_crud_probe` | Google Calendar の作成・検証・更新・削除を実行し、cleanup manifest と段階結果を返す。 | env、state、run_id | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_google_probe.py:194-372 | methods/e2e-google-02.puml |
| F211 | `e2e_google_probe.cleanup_google_calendar_crud_probe` | Google Calendar E2E manifest が所有する残存イベントを回収する。 | env、state、expected_run_id | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_google_probe.py:375-469 | methods/e2e-google-02.puml |
| F212 | `e2e_notion_probe.fetch` | Workers fetch の呼び出し形式差を吸収する。 | url、options | Any | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:39-50 | methods/e2e-notion-01.puml |
| F213 | `e2e_notion_probe._env_text` | Worker 環境変数から文字列設定を安全に取得する。 | env、key | str | なし | workers/src/e2e_notion_probe.py:53-57 | methods/e2e-notion-01.puml |
| F214 | `e2e_notion_probe._canonical_id` | Notion ID を区切りなしの標準形式へ正規化する。 | value | str | なし | workers/src/e2e_notion_probe.py:60-64 | methods/e2e-notion-01.puml |
| F215 | `e2e_notion_probe._fingerprint` | 正規化した検証対象を SHA-256 指紋へ変換する。 | value | str | なし | workers/src/e2e_notion_probe.py:67-68 | methods/e2e-notion-01.puml |
| F216 | `e2e_notion_probe._new_run_id` | 時刻と乱数から形式制約を満たす E2E run ID を生成する。 | なし | str | なし | workers/src/e2e_notion_probe.py:71-73 | methods/e2e-notion-01.puml |
| F217 | `e2e_notion_probe._run_marker` | E2E 資源を run ID に結び付ける識別マーカーを生成する。 | run_id | str | なし | workers/src/e2e_notion_probe.py:76-77 | methods/e2e-notion-01.puml |
| F218 | `e2e_notion_probe._rich_text` | 文字列を Notion rich_text 要素へ変換する。 | content | dict | なし | workers/src/e2e_notion_probe.py:80-81 | methods/e2e-notion-01.puml |
| F219 | `e2e_notion_probe._title` | 文字列を Notion title 要素へ変換する。 | content | dict | なし | workers/src/e2e_notion_probe.py:84-85 | methods/e2e-notion-01.puml |
| F220 | `e2e_notion_probe._header_text` | HTTP 応答ヘッダから正規化済み文字列を取得する。 | response、name | str | なし | workers/src/e2e_notion_probe.py:88-96 | methods/e2e-notion-01.puml |
| F221 | `e2e_notion_probe._retry_after_seconds` | rate limit 応答から再試行待機秒数を安全に算出する。 | response | float \| None | なし | workers/src/e2e_notion_probe.py:99-104 | methods/e2e-notion-01.puml |
| F222 | `e2e_notion_probe._notion_request` | 小さなNotion JSON APIを呼び、429だけ公式待機値で再試行する。 | env、method、path、payload | tuple[int, Any, int] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:107-155 | methods/e2e-notion-01.puml |
| F223 | `e2e_notion_probe._request_stage` | E2E API 操作を実行し、manifest の段階状態と失敗情報を更新する。 | env、stages、retries、key、method、path、payload | tuple[int, Any] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:158-171 | methods/e2e-notion-02.puml |
| F224 | `e2e_notion_probe._schema_matches` | Notion database schema が E2E の必須プロパティを満たすか判定する。 | database、expected | bool | なし | workers/src/e2e_notion_probe.py:174-182 | methods/e2e-notion-02.puml |
| F225 | `e2e_notion_probe._verify_database` | Notion database を取得し、対象 ID と schema を検証する。 | env、database_id、expected_schema、stages、retries、stage_key、error_prefix | str | なし | workers/src/e2e_notion_probe.py:185-211 | methods/e2e-notion-02.puml |
| F226 | `e2e_notion_probe._verify_targets` | E2E 操作対象の設定値を検査し、安全な識別指紋を返す。 | env、stages、retries | str | なし | workers/src/e2e_notion_probe.py:214-249 | methods/e2e-notion-02.puml |
| F227 | `e2e_notion_probe._property_text` | Notion page property から文字列値を取り出す。 | page、property_name、property_type | str | なし | workers/src/e2e_notion_probe.py:252-271 | methods/e2e-notion-02.puml |
| F228 | `e2e_notion_probe._property_number` | Notion page property から数値を取り出す。 | page、property_name | int \| None | なし | workers/src/e2e_notion_probe.py:274-284 | methods/e2e-notion-02.puml |
| F229 | `e2e_notion_probe._page_database_id` | Notion page の親 database ID を標準形式で取得する。 | page | str | なし | workers/src/e2e_notion_probe.py:287-291 | methods/e2e-notion-02.puml |
| F230 | `e2e_notion_probe._page_archived` | Notion page がアーカイブ済みか判定する。 | page | bool | なし | workers/src/e2e_notion_probe.py:294-295 | methods/e2e-notion-02.puml |
| F231 | `e2e_notion_probe._page_has_marker` | Notion page が指定 E2E マーカーを持つか判定する。 | page、page_id、database_id、marker_property、marker | bool | なし | workers/src/e2e_notion_probe.py:298-310 | methods/e2e-notion-02.puml |
| F232 | `e2e_notion_probe._event_page_matches` | Notion event ページが E2E の期待値と一致するか判定する。 | page、page_id、database_id、marker、title、content、location、page_uuid | bool | なし | workers/src/e2e_notion_probe.py:313-344 | methods/e2e-notion-02.puml |
| F233 | `e2e_notion_probe._qa_page_matches` | Notion Q&A ページが E2E の期待値と一致するか判定する。 | page、page_id、database_id、marker、question、answer、number | bool | なし | workers/src/e2e_notion_probe.py:347-369 | methods/e2e-notion-02.puml |
| F234 | `e2e_notion_probe._event_create_payload` | Notion event 検証ページの作成 payload を組み立てる。 | database_id、run_id | tuple[dict, dict] | なし | workers/src/e2e_notion_probe.py:372-403 | methods/e2e-notion-03.puml |
| F235 | `e2e_notion_probe._event_update_payload` | Notion event 検証ページの更新 payload を組み立てる。 | run_id、page_id | tuple[dict, dict] | なし | workers/src/e2e_notion_probe.py:406-423 | methods/e2e-notion-03.puml |
| F236 | `e2e_notion_probe._qa_create_payload` | Notion Q&A 検証ページの作成 payload を組み立てる。 | database_id、run_id | tuple[dict, dict] | なし | workers/src/e2e_notion_probe.py:426-443 | methods/e2e-notion-03.puml |
| F237 | `e2e_notion_probe._qa_update_payload` | Notion Q&A 検証ページの更新 payload を組み立てる。 | run_id | tuple[dict, dict] | なし | workers/src/e2e_notion_probe.py:446-462 | methods/e2e-notion-03.puml |
| F238 | `e2e_notion_probe._find_page_by_marker` | 指定マーカーの Notion E2E ページを検索する。 | env、database_id、marker_property、marker、stages、retries、stage_key、error_prefix | tuple[str, str] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:465-510 | methods/e2e-notion-03.puml |
| F239 | `e2e_notion_probe._archive_page` | E2E が所有する Notion ページをアーカイブする。 | env、database_id、page_id、marker_property、marker、stages、retries、stage_prefix | tuple[bool, int, int, str] | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:513-573 | methods/e2e-notion-03.puml |
| F240 | `e2e_notion_probe._cleanup_resources` | manifest に記録された E2E 所有資源を回収する。 | env、manifest | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:576-665 | methods/e2e-notion-03.puml |
| F241 | `e2e_notion_probe._target_fingerprints` | 操作対象 ID を値そのものではなく SHA-256 指紋へ変換する。 | event_database_id、qa_database_id | dict[str, str] | なし | workers/src/e2e_notion_probe.py:668-672 | methods/e2e-notion-03.puml |
| F242 | `e2e_notion_probe._clean_manifest` | 成功済み E2E manifest を秘密値のない確定状態へ整形する。 | run_id、outcome、cleanup_attempts、stages、event_database_id、qa_database_id、event_page_id、qa_page_id、started_at | dict | なし | workers/src/e2e_notion_probe.py:675-703 | methods/e2e-notion-03.puml |
| F243 | `e2e_notion_probe.run_notion_crud_probe` | Notion の作成・検証・更新・アーカイブを実行し、cleanup manifest と段階結果を返す。 | env、state、run_id | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:706-1089 | methods/e2e-notion-03.puml |
| F244 | `e2e_notion_probe.cleanup_notion_crud_probe` | Notion E2E manifest が所有する残存ページを回収する。 | env、state、expected_run_id | dict | 外部 API 呼び出し、永続状態の参照または更新 | workers/src/e2e_notion_probe.py:1092-1185 | methods/e2e-notion-03.puml |
| F245 | `tools.validate_e2e_mcp_config._expect` | 条件を検査し、不一致を検証エラーとして収集する。 | errors、condition、code | None | なし | tools/validate_e2e_mcp_config.py:88-90 | methods/e2e-validator-01.puml |
| F246 | `tools.validate_e2e_mcp_config._approval_modes` | MCP 設定から承認モードの組み合わせを抽出する。 | server | dict[str, str] | なし | tools/validate_e2e_mcp_config.py:93-101 | methods/e2e-validator-01.puml |
| F247 | `tools.validate_e2e_mcp_config._check_approvals` | E2E MCP の承認設定が安全側の許可条件を満たすか検査する。 | errors、server、read_tools、write_tools、prefix | None | ファイル参照 | tools/validate_e2e_mcp_config.py:104-115 | methods/e2e-validator-01.puml |
| F248 | `tools.validate_e2e_mcp_config._check_common_server` | MCP server の共通 command、環境変数、引数制約を検査する。 | errors、server、prefix | None | ファイル参照 | tools/validate_e2e_mcp_config.py:118-145 | methods/e2e-validator-01.puml |
| F249 | `tools.validate_e2e_mcp_config._check_project_config` | プロジェクトローカル MCP 設定を読み、安全制約を検査する。 | config | list[str] | ファイル参照 | tools/validate_e2e_mcp_config.py:148-334 | methods/e2e-validator-01.puml |
| F250 | `tools.validate_e2e_mcp_config._check_packages` | E2E で利用する MCP package と固定条件を検査する。 | package、lock | list[str] | ファイル参照 | tools/validate_e2e_mcp_config.py:337-370 | methods/e2e-validator-01.puml |
| F251 | `tools.validate_e2e_mcp_config.main` | 検証を実行し、結果を標準出力と終了コードで返す。 | なし | int | ファイル参照、標準出力 | tools/validate_e2e_mcp_config.py:373-400 | methods/e2e-validator-01.puml |
| F252 | `tools.validate_e2e_secret_hygiene._git` | Git コマンドを読み取り専用で実行し、標準出力を返す。 | *args | subprocess.CompletedProcess[bytes] | 外部プロセス実行 | tools/validate_e2e_secret_hygiene.py:53-60 | methods/e2e-validator-01.puml |
| F253 | `tools.validate_e2e_secret_hygiene._tracked_paths` | Git の追跡対象パス一覧を取得する。 | なし | list[str] | ファイル参照 | tools/validate_e2e_secret_hygiene.py:63-71 | methods/e2e-validator-01.puml |
| F254 | `tools.validate_e2e_secret_hygiene._is_forbidden_tracked_path` | 追跡対象パスが秘密ファイル禁止パターンに該当するか判定する。 | path | bool | なし | tools/validate_e2e_secret_hygiene.py:74-84 | methods/e2e-validator-01.puml |
| F255 | `tools.validate_e2e_secret_hygiene._validate_template` | E2E 設定テンプレートに実値や禁止キーが含まれないか検査する。 | なし | list[str] | ファイル参照 | tools/validate_e2e_secret_hygiene.py:87-115 | methods/e2e-validator-01.puml |
| F256 | `tools.validate_e2e_secret_hygiene.main` | 検証を実行し、結果を標準出力と終了コードで返す。 | なし | int | ファイル参照、標準出力 | tools/validate_e2e_secret_hygiene.py:118-146 | methods/e2e-validator-01.puml |
| F257 | `tools.validate_e2e_workflow._expect` | 条件を検査し、不一致を検証エラーとして収集する。 | errors、condition、code | None | なし | tools/validate_e2e_workflow.py:35-37 | methods/e2e-validator-02.puml |
| F258 | `tools.validate_e2e_workflow._step_block` | GitHub Actions workflow から指定 step の YAML ブロックを抽出する。 | text、name | str | なし | tools/validate_e2e_workflow.py:40-46 | methods/e2e-validator-02.puml |
| F259 | `tools.validate_e2e_workflow._check_action_pins` | workflow の外部 action が完全 SHA に固定されているか検査する。 | errors、text | None | ファイル参照 | tools/validate_e2e_workflow.py:49-65 | methods/e2e-validator-02.puml |
| F260 | `tools.validate_e2e_workflow._check_workflow` | E2E workflow の trigger、権限、手順、失敗時 cleanup を検査する。 | text | list[str] | ファイル参照 | tools/validate_e2e_workflow.py:68-130 | methods/e2e-validator-02.puml |
| F261 | `tools.validate_e2e_workflow.main` | 検証を実行し、結果を標準出力と終了コードで返す。 | なし | int | ファイル参照、標準出力 | tools/validate_e2e_workflow.py:133-145 | methods/e2e-validator-02.puml |

## 信頼境界

| ID | 境界 | 理由 | 根拠 | 確実性 |
|---|---|---|---|---|
| Z001 | 外部呼び出し・トリガー | ネットワーク到達性と起動主体が Worker 内部と異なる | `workers/src/entry.py:58-153`、`workers/wrangler.jsonc:61-65` | confirmed |
| Z002 | Cloudflare Worker runtime | Secret 参照と binding 権限を持つ実行境界 | `workers/src/entry.py:50-56`、`workers/wrangler.jsonc:2-7` | confirmed |
| Z003 | Cloudflare managed state | 永続データ責任と分離単位が変わる | `workers/wrangler.jsonc:9-23`、`workers/wrangler.jsonc:66-74` | confirmed |
| Z004 | Google services | OAuth 主体、アカウント、外部運用主体が変わる | `workers/src/google_auth.py:392-450` | confirmed |
| Z005 | Notion service | Integration 主体と Workspace のデータ責任が変わる | `workers/src/google_apply_sync.py:194-408` | confirmed |
| Z006 | Discord service | Bot 主体と Guild/Channel 認可が変わる | `workers/src/discord_notion_sync.py:213-304` | confirmed |
| Z007 | token broker | 運用主体、資格情報移送、ネットワーク境界が実行時設定に依存する | `workers/src/google_auth.py:124-176` | runtime-unverified |

## 未確認事項

1. Cloudflare 上の Secret、実 binding、外部 API 権限、疎通結果はローカル静的検査では確定できない。
2. token broker の設定有無、提供者、実際の認証方式は実環境で確認が必要。
3. `/gcal/webhook` の Edge 側制御、token 付き Google watch の登録、実通知は運用環境で確認が必要。
4. 外部 API の作成・更新・削除、レート制限、再試行の実行成功は未検証。
5. `new_sqlite_classes` は Durable Object の移行指定であり、Cloudflare D1 binding を示さない。
