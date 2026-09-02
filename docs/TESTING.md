# ローカルテスト

## 目的

この文書は、IE Event Bot の単体テストを Linux / WSL の CPython 上で再現する手順と、テストで保証できる境界を定義する。

テストは Cloudflare、Discord、Google、Notion の実環境へ接続しない。認証情報も使用しない。

## セットアップ

リポジトリルートの仮想環境へ開発依存を導入する。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install -r workers/requirements.txt
```

既存の `.venv` が利用できる場合は、作り直す必要はない。

## 実行方法

全テスト:

```bash
.venv/bin/python -m pytest -q
```

ファイル単位:

```bash
.venv/bin/python -m pytest -q tests/test_entry.py
```

テスト単位:

```bash
.venv/bin/python -m pytest -q \
  tests/test_entry.py::test_webhook_duplicate_dispatches_only_once
```

CI は Ruff、Pyright の後に全テストを実行する。テストが収集できない場合も成功扱いにはしない。

E2E オーケストレーター MCP のローカル契約テストと設定検査:

```bash
npm run test:mcp
.venv/bin/python tools/validate_e2e_mcp_config.py
.venv/bin/python tools/validate_e2e_secret_hygiene.py
.venv/bin/python tools/validate_e2e_workflow.py
bash -n tools/configure_github_e2e_environment.sh
```

これらも通信先をテスト代替へ差し替えるか、設定ファイルだけを読む。実 Worker や外部サービスへは接続しない。

## 手動 E2E workflow

`.github/workflows/e2e-staging.yml` は `workflow_dispatch` 専用であり、PR、push、schedule からは起動しない。forkではこのworkflowを登録するため、既定ブランチを`develop`とする。最初の job でローカル検査と Wrangler dry-runを行い、成功後に `e2e` GitHub Environment の承認を待つ。`GITHUB_TOKEN` は `contents: read` だけに限定する。

実行モード:

| モード | 外部操作 |
| --- | --- |
| `preflight` | E2E Worker の health とマスク済み status を読む。既定値であり、deploy と外部 CRUD は行わない |
| `deploy-and-crud-smoke` | 専用 Worker を deploy し、Google、Discord、Notion の自己 cleanup 型 CRUD probe と所有状態確認を順に行う |
| `deploy-and-discord-google-smoke` | 専用 Worker を deploy し、Discord Scheduled Event を既存の適用処理で Google event へ反映して検証後、両資源を cleanup する |
| `deploy-and-discord-notion-smoke` | 専用 Worker を deploy し、Discord Scheduled Event を既存の適用処理で Notion 内部 DB へ反映して検証後、両資源を cleanup する |
| `deploy-and-google-discord-smoke` | 専用 Worker を deploy し、Google event を既存の適用処理で Discord Scheduled Event へ反映して検証後、両資源を cleanup する |
| `deploy-and-google-notion-smoke` | 専用 Worker を deploy し、Google event を既存の適用処理で Notion 内部 DB へ反映して検証後、両資源を cleanup する |
| `deploy-and-qa-notification-smoke` | 専用 Worker を deploy し、所有Q&A pageの初回抑止と更新通知を検証後、Notion pageとDiscord messageをcleanupする |
| `deploy-and-reminder-smoke` | 専用 Worker を deploy し、所有 Scheduled Event の前日通知と重複抑止を検証後、Discord event と message を cleanup する |
| `deploy-and-notion-cleanup-smoke` | 専用 Worker を deploy し、所有する期限到来・将来日時の Notion page だけで期限判定と interval guard を検証後、両 page を cleanup する |
| `deploy-and-webhook-simulation-smoke` | 専用 Worker を deploy し、所有 Google event を差分取得・同期 dispatch 経由で Notion へ反映して検証後、両資源を cleanup する |

書き込みモードは、各 `seed_fixture`、`trigger_sync`、または所有資源限定の `trigger_job` の監査開始記録がある service / scenario だけを run ID 付きで cleanup する。実行 CLI 内の cleanup に加え、workflow の `always()` step でも一時失敗を最大3回再試行する。所有権不一致、旧 manifest、対象 fingerprint 不一致は再試行せず、他 run の資源を削除しない。

実行前に `e2e` Environment に Secret 5件が設定済みで、variableが0件であることを値を表示せず確認する。Worker URLとそのfingerprintもActionsログでマスクするためSecretとして扱う。設定helperは同名の旧variableがあればSecret登録後に削除する。Google、Discord、Notion の実行時 Secret は Cloudflare Worker だけに保持し、GitHub Actions へ複製しない。

artifact は JUnit XML、マスク済み MCP 監査要約、run manifest だけを14日保持する。run manifest の Worker URL、version、watch、外部資源は SHA-256 fingerprint または真偽値であり、生の識別子、token、request / response 本文を保存しない。

Google→Notion モードは、専用 Calendar に一意な event を作成して読み戻し、現行の `apply_google_events` へ渡し、専用 Notion 内部 DB に作られた page の内容を確認する。外部 Notion DB が空、`DISCORD_SYNC_ENABLED=false`、既定の Notion プロパティ名であることを事前に強制し、適用処理には一時状態を渡すため、同期対応表と再試行キューを KV へ保存しない。Google 認証 token の取得・更新に伴う認証 cache はこの制限の対象外である。

Google→Discord モードは、専用 Calendar に一意な event を作成して読み戻し、現行の `_sync_to_discord` へ1件だけ渡し、専用 Guild に作られた Scheduled Event を確認する。通常設定の `DISCORD_SYNC_ENABLED=false` は維持し、この関数呼び出しだけを一時的に有効化する。Notion、同期対応表、再試行 queue は使用しない。作成応答を失った場合は run marker で一意に再探索し、0件または複数件なら clean と推測せず dirty を維持する。

Discord→Notion モードは、専用 Guild に一意な Scheduled Event を作成して読み戻し、現行の `_sync_discord_event_upsert` へ1件だけ渡し、専用 Notion 内部 DB に作られた page を確認する。`DISCORD_TO_GOOGLE_SYNC_ENABLED=false`、外部 Notion DB が空、既定の Notion プロパティ名であることを事前に強制し、通常の Discord snapshot / queue と作成通知は使用しない。作成応答を失った場合は run marker または Discord event ID で一意に再探索し、所有権が未解決なら dirty を維持する。

Discord→Google モードは、専用 Guild に一意な Scheduled Event を作成して読み戻し、現行の `_sync_discord_event_upsert` へ1件だけ渡し、専用 Calendar に作られた event を Discord event ID の private extended property で検索して内容を確認する。通常設定の `DISCORD_TO_GOOGLE_SYNC_ENABLED=false` は維持し、この関数呼び出しだけを有効化する env view では内部・外部 Notion DB を空にする。通常の Discord snapshot / queue と作成通知は使用しない。Google 認証 token の取得・更新に伴う認証 cache は更新され得る。作成結果を確定できず検索結果も0件の場合は clean と推測せず dirty を維持する。

QA通知モードは、専用 Q&A DB に run marker付きの未回答pageを1件作り、実行内cacheで初回通知が抑止されることを確認する。pageの質問を更新して読み戻した後、実行内cacheに更新前markerを保持し、通常ジョブと共通の `_run_qa_notification_pages` へその1件だけを渡して、専用Discordチャンネルに作られたmessageを読戻す。Notionの更新時刻が即時更新の前後で同値の場合は、run内だけの旧markerでcache missを作る。共有KVの `qa_cache`、Q&A DB全件取得、質問番号補完は使用しない。作成応答を失った場合はrun markerで再探索し、所有権が未解決ならdirtyを維持する。

前日リマインドモードは、現在時刻から24時間後の通知ウィンドウ内に開始する run marker 付き外部 Scheduled Event を専用 Guild へ1件作成して読み戻し、通常ジョブと共通の `_run_reminder_events` へその1件だけを渡す。専用チャンネルの message 本文、対象 role だけを許可した mention、実行内 cache 更新を確認し、同じ event を再度渡して message が増えないことを検証する。共有 KV の `reminder_cache`、Guild の通常 event 一覧処理、実 Cron は使用しない。event と message の作成応答を失った場合は run marker で再探索し、所有権が未解決なら dirty を維持する。

Notion cleanup モードは、専用内部 DB に run marker が異なる期限到来 page と将来日時 page を1件ずつ作成して読み戻し、通常ジョブと共通の `_run_auto_clean_pages` へその2件だけを渡す。期限到来 page だけが archive され、将来日時 page が残り、同じ時刻の2回目は interval guard で skip されることを確認する。fixture日時は分境界へ揃え、Notionによる `Z` とUTC offset等の表記正規化を許容して、RFC 3339上の同一時刻として比較する。実行時刻は probe 内状態へ閉じ込めるため、内部 DB の通常全件取得、共有 KV の `cleanup:last_epoch`、実 Cron は使用しない。作成応答を失った場合は page ごとに異なる run marker で再探索し、所有権が未解決なら dirty を維持する。

Webhook simulation モードは、専用 Calendar に run marker 付き event を1件作成し、通常同期と共通の Google 差分取得と `_run_sync_dispatch` を通す。取得結果から event ID と run marker が両方一致する1件だけを `apply_google_events` へ渡し、専用 Notion 内部 DB の page を確認して両資源を cleanup する。同期 cursor、最終実行時刻、最終結果、Google 認証 cache は request 内状態へ閉じ込め、共有 KV の対応表と queue も更新しない。このモードは `/gcal/webhook` への Google からの配信、watch channel、Webhook token、message-number 重複抑止、実 Cron を検証しない。

MCP の `trigger_sync` は固定 `scenario` 列挙に応じ、`/sync/all` ではなく `/admin/e2e/google-notion-sync`、`/admin/e2e/google-discord-sync`、`/admin/e2e/discord-notion-sync`、`/admin/e2e/discord-google-sync` のいずれかを呼ぶ。これらが確認するのは source event の作成・読取からアプリケーション適用処理を経た下流資源作成までであり、Google / Discord の差分取得、同期 cursor / snapshot / queue、全体同期、実 webhook / Cron 配信、Playwright によるブラウザ表示は保証しない。

`trigger_job` の `qa_check`、`reminder`、`cleanup` は、それぞれ所有資源限定の `/admin/e2e/qa-notification`、`/admin/e2e/reminder`、`/admin/e2e/notion-cleanup` を呼び、通常の `/jobs/qa-check`、`/jobs/reminder`、`/jobs/cleanup` は呼ばない。`trigger_webhook` は所有資源限定の `/admin/e2e/trigger-webhook` を呼び、通常の `/gcal/webhook` は呼ばない。run-all、通常の同期・Webhook・ジョブ route は、下流資源と共有状態を run ID で所有・回収できるまで実行しない。E2E Worker は `E2E_ORCHESTRATED_WRITES_ENABLED=false` で通常 route を `404` にし、preflight はこの既定拒否と8つの所有資源限定 route の有効状態を別々に確認する。残作業は [GitHub Issue #17](https://github.com/lycanthr0pes/IE_Event_Bot_fork/issues/17) で追跡する。

## テスト構成

| ファイル | 対象 |
| --- | --- |
| `tests/conftest.py` | `workers` ランタイムの最小代替と外部通信の遮断 |
| `tests/fakes.py` | HTTP Request、Workers KV、Durable Object storage / namespace |
| `tests/test_entry.py` | HTTP 認可の fail-closed、Webhook token、クールダウン、排他、Webhook 重複抑止 |
| `tests/test_google_watch.py` | channel token の必須・最大長・登録、旧 watch と token 変更時の更新、外部エラー本文の非公開 |
| `tests/test_state.py` | KV 読み書き、重複抑止、Durable Object 優先経路 |
| `tests/test_sync_lock_do.py` | ロック競合・解放、Webhook 重複レコードの期限 |
| `tests/test_sync_queues.py` | Google / Discord 同期の件数制限、失敗と残件の繰り越し |
| `tests/test_e2e_entry.py` | E2E route allowlist、run ID、status のマスキング、Cron 無効化 |
| `tests/test_e2e_*_probe.py` | 外部通信を差し替えた CRUD、サービス間適用、QA通知、前日リマインド、Notion期限cleanup、Webhook simulation、DO manifest、cleanup、応答喪失、rate limit |
| `tests/test_jobs.py` | Q&A更新通知、前日リマインド、Notion期限cleanupの共通処理と実行内状態 |
| `tools/e2e_mcp_server.test.mjs` | MCP tool allowlist、接続先 fingerprint、承認、skip 判定、run manifest |
| `tools/run_e2e_workflow.test.mjs` | workflow 順序、途中失敗時 cleanup、再試行、監査対象、evidence の固定エラー |

## 外部通信の扱い

`tests/conftest.py` が Cloudflare Python Workers の `fetch` を、常にテスト失敗にする関数へ置き換える。外部 API を扱うテストは、対象モジュールの通信境界を `monkeypatch` で明示的に差し替える。

テストへ実トークン、サービスアカウント、実 DB ID、実チャンネル ID を渡してはならない。fixture には `test-token` など用途が明らかなダミー値を使う。

## 非同期コードの扱い

追加依存を増やさず、各テストは `asyncio.run()` で非同期処理を実行する。テスト内でイベントループを共有する必要が生じた場合だけ、非同期テスト用依存の追加を検討する。

## このテストで保証しないこと

ローカルテストは次を確認しない。

- Cloudflare の Python Workers、Workers KV、Durable Objects の実ランタイム互換性
- `workers/wrangler.jsonc` と Cloudflare 管理画面側バインディングの一致
- Discord、Google、Notion の現在の API 仕様、権限、レート制限、データ内容
- Cron、Google watch、Webhook の実配信
- デプロイ後の疎通、性能、可用性

これらはローカル単体テストと分け、認証情報と実行許可を確認したうえで preview または実環境の検証として扱う。

## テスト追加時の方針

- 1テストにつき、判定したい振る舞いを1つに絞る。
- 時刻、UUID、外部 API 応答は必要な境界で固定する。
- 成功だけでなく、失敗、再試行、件数上限、空データを確認する。
- 本番コードの内部実装ではなく、返り値と保存状態を優先して検証する。
- テスト後に `ruff check .`、`pyright`、`git diff --check` も実行する。
