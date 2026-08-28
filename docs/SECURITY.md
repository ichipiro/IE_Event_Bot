# セキュリティ設計

## 適用範囲

この文書は、リポジトリ内のコードと設定から確認できる認証、シークレット、保存データ、外部 API の安全要件をまとめる。Cloudflare や各外部サービスの管理画面だけにある設定は、ローカル検査では確認できない。

## HTTP 認可

`workers/src/entry.py` の現行実装:

| ルート | Bearer 認可 |
| --- | --- |
| `GET /health` | なし |
| `POST /gcal/webhook` | なし |
| `/sync/*`、`/gcal/sync` | `_authorized()` |
| `/admin/*` | `_authorized()` |
| `/jobs/*` | `_authorized()` |

`_authorized()` は `INTERNAL_API_TOKEN` が未設定の場合に認可成功を返す。そのため、本番では `INTERNAL_API_TOKEN` を必ず Wrangler Secret として設定する。未設定を安全な既定値とは扱わない。

`/gcal/webhook` は Google の通知ヘッダーを重複抑止に使うが、現行コードでは Bearer 認可や共有シークレット照合を行わない。通知ヘッダーだけを送信元認証として扱わず、公開エンドポイントであることを前提に、Cloudflare 側の制御と同期側のレート・重複対策を確認する。

## シークレット

主なシークレット:

- `INTERNAL_API_TOKEN`
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

## 外部 API

- Discord、Google、Notion へ送るトークンには、必要最小限の権限を付与する。
- Google サービスアカウントは対象カレンダーだけへ必要な権限を付与する。
- Notion インテグレーションは同期対象データベースだけへ接続する。
- Discord Bot は必要な Guild、チャンネル、Scheduled Event 操作に限定する。
- API エラーを記録するときも、Authorization ヘッダーやレスポンス中の機密値を残さない。

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
- API トークンの権限と有効期限
- GitHub の Secret、Actions 権限、branch protection、ruleset
- 実際の疎通、レート制限、監査ログ
