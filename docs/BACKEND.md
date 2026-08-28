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
    └── Durable Object: ロック、最終同期時刻、Webhook 重複抑止
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

1. `/gcal/webhook` で通知を受ける。
2. `X-Goog-Channel-ID` と `X-Goog-Message-Number` を使って重複を判定する。
3. 未処理なら全体同期を実行する。
4. 正常時は本文なしの `204` を返す。

### Cron

`workers/wrangler.jsonc` の Cron は、各 `CRON_ENABLE_*` 変数に従って同期、watch 確認、Q&A、リマインド、クリーンアップを実行する。

## 状態管理

- 即時整合性と競合回避が必要な小さな状態は Durable Object に置く。
- 比較的広い参照用状態、対応表、キュー、キャッシュ、診断結果は Workers KV に置く。
- Durable Object バインディングがない場合、一部の状態処理は KV へフォールバックする。
- `STATE_KV` 自体がない場合、永続状態を使う処理は無効または限定動作になる。
- 現行構成は Workers KV と Durable Object であり、Cloudflare D1 は使用していない。

詳細は `docs/DB-SCHEMA.md` と `docs/do-kv-design.md` を参照する。

## 外部 API と失敗処理

- 各同期モジュールは Cloudflare Workers の `fetch` を介して外部 API を呼ぶ。
- 同期件数の上限を超えた項目や失敗した項目は、KV のキューへ繰り越す。
- 管理診断は `/admin/migration-status`、外部疎通を含む診断は `include_checks=1` で行う。
- 外部 API の実行結果は、ローカルの Lint や型検査では保証できない。

## 設定の正本

- Worker、バインディング、変数、Cron: `workers/wrangler.jsonc`
- Python 依存: `pyproject.toml`、`workers/requirements.txt`
- HTTP と Cron の実装: `workers/src/entry.py`
- 詳細な機能一覧: `README.md`、`docs/Event_Bot仕様書.md`
