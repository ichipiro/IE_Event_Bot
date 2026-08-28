# フロントエンド設計

## 現在の状態

このリポジトリには、ブラウザー画面、モバイル画面、デスクトップ画面などのフロントエンド実装は存在しない。HTML、CSS、JavaScript の画面資産や、フロントエンド用パッケージ管理設定も確認されていない。

利用者または運用者との境界は、Cloudflare Worker が提供する HTTP API と Discord、Google Calendar、Notion の各サービス画面である。

## HTTP インターフェース

`workers/src/entry.py` が JSON または空レスポンスを返す。

| 種別 | 主なパス | 用途 |
| --- | --- | --- |
| 状態確認 | `GET /health` | Worker と KV バインディングの基本確認 |
| Webhook | `POST /gcal/webhook` | Google Calendar 通知の受信 |
| 同期 | `/sync/*`、`/gcal/sync` | 手動同期 |
| 管理 | `/admin/*` | トークン投入、watch、診断 |
| ジョブ | `/jobs/*` | Q&A、リマインド、クリーンアップ |

認可要件は `docs/SECURITY.md` を参照する。

## 表示責務

- Worker は管理画面を描画しない。
- エラーは HTTP ステータスと JSON の `ok`、`error`、詳細項目で表す。
- 日時や通知本文の表示形式は、Discord、Google Calendar、Notion へ送るデータの組み立て処理で決まる。
- API レスポンスを表示する専用クライアントは、このリポジトリの範囲外である。

## 将来フロントエンドを追加する場合

追加前に、少なくとも次を明文化する。

1. 対象利用者と操作範囲
2. Worker API の認証方式
3. CORS、Cookie、セッション、CSRF の扱い
4. シークレットをブラウザーへ渡さない設計
5. API エラーと再試行の表示
6. アクセシビリティと対応ブラウザー
7. ビルド、Lint、型検査、テストの追加要件

フロントエンドが存在しない現状では、画面上の表示やブラウザー動作を検証済みとは扱わない。
