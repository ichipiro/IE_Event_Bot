# バックエンド設計

## 概要

IE Event Bot は Cloudflare Python Workers 上で動作し、Discord、Google Calendar、Notion のイベント情報を同期する。HTTP リクエストと Cron を入口に、外部 API 呼び出し、状態保存、通知、保守ジョブを実行する。

```text
HTTP / Cron
    │
    ▼
workers/src/entry.py
    │
    ├── Google Calendar 差分取得
    ├── Notion / Discord への反映
    ├── Discord 差分取得と Google / Notion への反映
    └── Q&A・リマインド・クリーンアップ
    │
    ▼
StateStore
    ├── Workers KV: カーソル、対応表、キュー、キャッシュ、診断結果
    └── Durable Object: ロック、最終同期時刻、Webhook 重複抑止、E2E cleanup 所有権
```

## モジュール

| ファイル | 責務 |
| --- | --- |
| `workers/src/entry.py` | HTTP ルーティング、Cron、認可、同期ディスパッチ |
| `workers/src/google_calendar_sync.py` | `updatedMin` を使う Google Calendar 差分取得 |
| `workers/src/google_apply_sync.py` | Google イベントの Notion / Discord 反映 |
| `workers/src/discord_notion_sync.py` | Discord の差分検出、Notion / Google 反映 |
| `workers/src/google_auth.py` | Google アクセストークンの優先順位、取得、KV キャッシュ |
| `workers/src/google_watch.py` | Google Calendar watch の登録・更新・期限確認 |
| `workers/src/jobs.py` | Q&A 通知、前日リマインド、Notion クリーンアップ |
| `workers/src/health_checks.py` | Notion、Discord、Google の疎通診断 |
| `workers/src/state.py` | 状態ストアの抽象化 |
| `workers/src/sync_lock_do.py` | `SyncCoordinator` Durable Object |
| `workers/src/e2e_entry.py` | E2E 専用 route allowlist、run ID、status、Cron 無効化 |
| `workers/src/e2e_*_probe.py` | 専用外部資源の CRUD、所有権確認、cleanup |

## HTTP ルート

`workers/src/entry.py` で確認できるルート:

- `GET /health`
- `POST /gcal/webhook`
- `GET|POST /sync/all`
- `GET|POST /gcal/sync`
- `GET|POST /sync/discord-notion`
- `POST /admin/google-token`
- `POST /admin/gcal/watch/ensure`
- `GET /admin/migration-status`
- `GET|POST /jobs/qa-check`
- `GET|POST /jobs/reminder`
- `GET|POST /jobs/cleanup`
- `GET|POST /jobs/run-all`

一部ルートはコード上で HTTP メソッドを厳密に限定していない。クライアントは上記の意図されたメソッドを使用する。

`/sync/*`、`/admin/*`、`/jobs/*` は `INTERNAL_API_TOKEN` が未設定でも拒否する。`/gcal/webhook` は Bearer 認可ではなく、Google watch に設定した `GCAL_WEBHOOK_TOKEN` と受信した `X-Goog-Channel-Token` を照合する。

## 同期フロー

### 全体同期

1. `SYNC_INTERVAL_SECONDS` と最終同期時刻からクールダウンを判定する。
2. 有効な場合は `SYNC_COORDINATOR` のロックを取得する。
3. Google Calendar の差分を取得する。
4. Google の変更を Notion と Discord へ反映する。
5. `SYNC_ALL_INCLUDE_DISCORD_NOTION` が有効なら、Discord の差分を Notion と Google へ反映する。
6. 成功時にカーソルと最終同期時刻を更新する。
7. 実行結果を `result:*` へ保存し、最後にロックを解放する。

### Google Webhook

1. `/gcal/webhook` で通知を受け、`X-Goog-Channel-Token` を検証する。
2. `X-Goog-Channel-ID` と `X-Goog-Message-Number` を使って重複を判定する。
3. 未処理なら全体同期を実行する。
4. 正常時は本文なしの `204` を返す。

### Cron

`workers/wrangler.jsonc` の Cron は、各 `CRON_ENABLE_*` 変数に従って同期、watch 確認、Q&A、リマインド、クリーンアップを実行する。

## 状態管理

- 即時整合性と競合回避が必要な小さな状態は Durable Object に置く。
- 比較的広い参照用状態、対応表、キュー、キャッシュ、診断結果は Workers KV に置く。
- Durable Object バインディングがない場合、一部の状態処理は KV へフォールバックする。
- E2E cleanup manifest は例外であり、Durable Object がない場合はフェイルクローズにする。
- `STATE_KV` 自体がない場合、永続状態を使う処理は無効または限定動作になる。
- 現行構成は Workers KV と Durable Object であり、Cloudflare D1 は使用していない。

追跡対象の正本は `docs/DB-SCHEMA.md` とコードである。`docs/do-kv-design.md` は追跡対象外のローカル補助であり、文書の分類は `docs/REFERENCES.md` を参照する。

## 外部 API と失敗処理

- 各同期モジュールは Cloudflare Workers の `fetch` を介して外部 API を呼ぶ。
- 同期件数の上限を超えた項目や失敗した項目は、KV のキューへ繰り越す。
- 管理診断は `/admin/migration-status`、外部疎通を含む診断は `include_checks=1` で行う。
- 外部 API の実行結果は、ローカルの Lint や型検査では保証できない。

## E2E 専用 Worker

`workers/wrangler.e2e.jsonc` は `workers/src/e2e_entry.py` を入口にする。E2E CRUD、Google→Notion / Discord、Discord→Notion / Google、QA通知、前日リマインドの専用 scenario、cleanup、status の route だけを明示的に公開し、書き込みには Bearer 認証、`POST`、所定形式の `X-E2E-Run-ID` を要求する。

Google→Notion scenario は、専用 Calendar の event を `apply_google_events` へ渡し、専用 Notion 内部 DB の page を確認後に両方を cleanup する。外部 Notion DB と Discord 反映を事前に拒否し、一時状態により通常の同期対応表と queue を変更しない。Google event ID、Notion page ID、対象 fingerprint は `google_notion` の Durable Object manifest で管理する。

Google→Discord scenario は、専用 Calendar の event を読み戻して既存の `_sync_to_discord` へ1件だけ渡し、作成された Scheduled Event の名前、説明中の run marker、Guild、場所を確認後に両方を cleanup する。通常設定の `DISCORD_SYNC_ENABLED=false` は維持し、この適用呼び出しだけを一時的な env view で有効化する。Notion、通常 KV の同期対応表、queue は変更しない。両 event ID と対象 fingerprint は `google_discord` の Durable Object manifest で管理する。

Discord→Notion scenario は、専用 Guild に一意な Scheduled Event を作成して読み戻し、既存の `_sync_discord_event_upsert` へ1件だけ渡し、専用 Notion 内部 DB の page を確認後に両方を cleanup する。Google 同期、外部 Notion DB、通常の Discord snapshot / queue、作成通知は使用しない。Discord event ID、Notion page ID、対象 fingerprint は `discord_notion` の Durable Object manifest で管理する。

Discord→Google scenario は、専用 Guild に一意な Scheduled Event を作成して読み戻し、既存の `_sync_discord_event_upsert` へ1件だけ渡し、専用 Calendar に作成された event の内容と private extended property を確認後に両方を cleanup する。通常設定の `DISCORD_TO_GOOGLE_SYNC_ENABLED=false` は維持し、この適用呼び出しだけを一時的な env view で有効化すると同時に内部・外部 Notion DB を隠す。通常の Discord snapshot / queue、作成通知、Notion は使用しない。Google 認証 token の取得・更新に伴う認証 cache は更新され得る。両 event ID と対象 fingerprint は `discord_google` の Durable Object manifest で管理する。

QA通知 scenario は、専用 Q&A DB に run marker 付きの未回答 page を1件作成し、初回抑止用 cache を probe 内へ初期化した後、pageを更新して読み戻す。実行内 cache に更新前 marker を保持してから、通常ジョブと共通の `_run_qa_notification_pages` へその1件だけを渡す。作成された Discord message を検証後、messageを削除しpageをarchiveする。通常の全件取得、質問番号補完、共有KVの `qa_cache` は使用せず、page ID、message ID、対象 fingerprint は `qa_notification` の Durable Object manifest で管理する。

前日リマインド scenario は、通知ウィンドウ内に開始する run marker 付き外部 Scheduled Event を専用 Guild へ1件作成し、通常ジョブと共通の `_run_reminder_events` へその1件だけを渡す。Discord message の本文と role mention、実行内 cache 更新、2回目の重複抑止を検証後、message と event を削除する。通常の Guild event 一覧処理、共有 KV の `reminder_cache`、実 Cron は使用せず、event ID、message ID、対象 fingerprint は `reminder` の Durable Object manifest で管理する。

通常の全体同期、Webhook simulation、通常のジョブ route、Notion cleanup の下流資源はまだ run ID で所有できないため、`E2E_ORCHESTRATED_WRITES_ENABLED` の既定値を `false` とし、該当 route を `404` で隠す。所有資源限定 route の `E2E_GOOGLE_NOTION_SYNC_ENABLED`、`E2E_GOOGLE_DISCORD_SYNC_ENABLED`、`E2E_DISCORD_NOTION_SYNC_ENABLED`、`E2E_DISCORD_GOOGLE_SYNC_ENABLED`、`E2E_QA_NOTIFICATION_ENABLED`、`E2E_REMINDER_ENABLED` とは別の境界である。

通常 Worker の token 登録、実 Google webhook、watch 作成、migration status は E2E entry から公開しない。scheduled handler も設定値にかかわらず空結果を返す。E2E status は実 ID や Secret を返さず、worker version、watch、外部資源を SHA-256 fingerprint と真偽値だけで要約する。

## 設定の正本

- Worker、バインディング、変数、Cron: `workers/wrangler.jsonc`
- Python 依存: `pyproject.toml`、`workers/requirements.txt`
- Wrangler: `package.json`、`package-lock.json`
- HTTP と Cron の実装: `workers/src/entry.py`
- 機能一覧: `README.md`
- 追跡対象外のローカル補助: `docs/REFERENCES.md` の分類を参照
