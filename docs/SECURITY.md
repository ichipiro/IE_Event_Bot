# セキュリティ設計

## 適用範囲

この文書は、リポジトリ内のコードと設定から確認できる認証、シークレット、保存データ、外部 API の安全要件をまとめる。Cloudflare や各外部サービスの管理画面だけにある設定は、ローカル検査では確認できない。

## HTTP 認可

`workers/src/entry.py` の現行実装:

| ルート | 認証・認可 |
| --- | --- |
| `GET /health` | 公開 |
| `POST /gcal/webhook` | `X-Goog-Channel-Token` |
| `/sync/*`、`/gcal/sync` | `_authorized()` |
| `/admin/*` | `_authorized()` |
| `/jobs/*` | `_authorized()` |

`_authorized()` は `INTERNAL_API_TOKEN` が未設定、空、欠落、不一致のいずれでも認可に失敗する。同期、管理、ジョブ API は処理開始前に `401` を返す。

`/gcal/webhook` は `GCAL_WEBHOOK_TOKEN` と `X-Goog-Channel-Token` を処理開始前に定時間比較する。Secret 未設定時は `503`、ヘッダーの欠落または不一致時は `401` を返し、重複状態の更新や同期を行わない。Google watch の登録要求にも同じ token を含める。

watch 状態には Secret の生値ではなく SHA-256 fingerprint だけを保存し、既存 watch に fingerprint がない場合や token を変更した場合は再登録する。fingerprint からの推測を避けるため、`GCAL_WEBHOOK_TOKEN` は十分長いランダム値とし、fingerprint もログへ出さない。重複抑止、クールダウン、Durable Object ロック、Cloudflare 側のレート制限は多層防御であり、token 検証の代替ではない。

Google watch API のエラー時は、外部応答本文を管理 API 応答や `last_result` へ流さず、HTTP status から作る内部エラーコードだけを返す。これにより、外部サービスが channel token をエラー本文へ反映した場合も保存・再応答しない。

## シークレット

主なシークレット:

- `INTERNAL_API_TOKEN`
- `GCAL_WEBHOOK_TOKEN`
- `NOTION_TOKEN`
- `DISCORD_TOKEN`
- `GOOGLE_API_BEARER_TOKEN`
- `GOOGLE_TOKEN_BROKER_AUTH`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SERVICE_ACCOUNT_JSON_B64`
- GitHub Actions の `RELEASE_AUTOMATION_TOKEN`

規則:

1. シークレットは `workers/wrangler.jsonc` の `vars`、Markdown、ログ、コミットへ保存しない。
2. Cloudflare のシークレットは `wrangler secret put` で登録する。
3. 値をチャット、画面共有、エラー本文へ出さない。
4. 漏えいの可能性がある場合は、対象サービスで失効・再発行する。
5. `workers/service-account.json` は `.gitignore` 対象だが、存在自体を安全性の保証とは扱わない。

## 保存データ

- Workers KV の `google:access_token` は機密情報である。
- `result:*`、キュー、スナップショット、対応表には外部サービス由来の識別子や内容が含まれ得る。
- `/admin/migration-status?include_checks=1` は外部サービスへ実際に接続するため、認可済みの運用者だけが実行する。
- KV は厳密なトランザクションストアではなく、最終的整合性を前提にする。
- Durable Object のロックと重複抑止は可用性・整合性対策であり、認証の代替ではない。
- E2E cleanup manifest は強整合な `SYNC_COORDINATOR` に保存し、KV の古い読み取りで dirty 所有権を上書きしない。
- 旧KV manifest が1件でも見つかった場合は、新しいE2E実行を止めて移行レビューを要求する。旧キーを自動削除・自動clean化しない。

## 外部 API

- Discord、Google、Notion へ送るトークンには、必要最小限の権限を付与する。
- Google サービスアカウントは対象カレンダーだけへ必要な権限を付与する。
- Notion インテグレーションは同期対象データベースだけへ接続する。
- Discord Bot は必要な Guild、チャンネル、Scheduled Event 操作に限定する。
- API エラーを記録するときも、Authorization ヘッダーやレスポンス中の機密値を残さない。

## E2E MCP 境界

- Worker origin は `ie-event-bot-e2e.*.workers.dev` の形だけでなく、別管理値 `E2E_WORKER_URL_SHA256` と完全一致した場合だけ利用する。
- MCP の書き込み tool は固定 route と run ID だけを受け取り、任意 URL や外部資源 ID を入力にしない。
- HTTP 200 でも `cooldown_skip` または `in_progress_skip` なら未実行として失敗扱いにする。
- status と run manifest は実 URL、Worker version ID、watch ID、外部資源 ID、token を返さず fingerprint だけを残す。
- create の成否を確定できず検索結果も 0 件の場合、clean と推測せず dirty を維持する。
- Google→Notion scenario は外部 Notion DB、Discord 反映、Notion プロパティ名の上書きを実行前に拒否し、同期対応表と queue を永続化しない。
- Google→Discord scenario は通常設定の Discord 同期を無効のまま保ち、専用 event 1件の適用呼び出しだけを有効化する。Notion と通常 KV の同期状態は変更しない。
- Discord→Notion scenario は Google 同期と外部 Notion DB を明示的に無効化し、専用 event 1件の適用呼び出しだけを行う。通常の Discord snapshot / queue と作成通知は変更しない。
- Discord→Google scenario は通常設定の Google 同期を無効のまま保ち、専用 event 1件の適用呼び出しだけを有効化し、内部・外部 Notion DB を一時 env view から隠す。通常の Discord snapshot / queue と作成通知は変更しない。
- Notion page の cleanup は DB、page、source event ID、run ID 入り title または content marker の一致を確認してから archive する。Google event も private source event ID、run ID 入り summary と description marker の一致を確認してから削除する。
- Discord Scheduled Event の cleanup は Guild、event ID、run ID 入りの名前と説明 marker がすべて一致した場合だけ削除する。
- deploy は Worker origin fingerprint を含む MCP 設定がすべて正常な場合だけ Wrangler を起動する。
- preflight は旧 KV manifest だけでなく、service / scenario の現行 Durable Object manifest が1件でも dirty なら失敗する。
- `trigger_sync` は任意 URL を受け取らず、通常の `/sync/all` ではなく、所有資源限定の Google→Notion / Discord と Discord→Notion / Google route だけを固定列挙から呼ぶ。
- 通常の同期、Webhook simulation、ジョブは下流資源と状態の cleanup 所有権が未実装であるため、E2E Worker では専用フラグを既定無効にし、preflight でも無効状態を確認する。

## E2E GitHub Actions 境界

- E2E workflow は手動起動だけを許可し、既定の `preflight` は read-only とする。
- Secret を使う job は required reviewer 付きの `e2e` Environment を参照し、`GITHUB_TOKEN` は `contents: read` に限定する。
- Worker URLとfingerprintはActionsログへの露出を防ぐため、GitHub Environment secretからだけ渡す。
- Cloudflare account ID と API token は deploy step だけへ渡し、cleanup と evidence へ継承しない。
- 外部 action は完全な commit SHA へ固定し、checkout 後の Git credential 永続化を無効にする。
- run ID と監査開始記録が一致する service / scenario だけを `always()` cleanup の対象にする。
- artifact は固定フィールドでマスクした監査要約と manifest に限定し、14日で失効させる。

## 設定値の扱い

`workers/wrangler.jsonc` にある Calendar、Notion、Discord、KV の ID は通常トークンではないが、運用対象を特定する情報である。不要な転載を避け、変更時は対象環境を確認する。

## 変更時の確認

- 新しいルートは、公開する理由がない限り `_authorized()` で保護する。
- 認可失敗は処理開始前に返す。
- 新しいログや診断レスポンスへシークレットを含めない。
- 新しい保存キーは、保持期間、機密性、整合性、削除方法を定義する。
- 依存関係を追加した場合は、供給元、保守状況、ライセンス、既知の脆弱性確認方法を記録する。

## 本番確認

次はローカル静的検査では未確認となるため、デプロイ時に別途確認する。

- Cloudflare 上の Secret 登録
- KV と Durable Object の実バインディング
- Webhook URL と外部サービス側の登録
- token 付き watch の再登録と `X-Goog-Channel-Token` を含む実通知
- 必要に応じた Cloudflare WAF とレート制限の設定
- API トークンの権限と有効期限
- GitHub の Secret、Actions 権限、branch protection、ruleset
- 実際の疎通と監査ログ
