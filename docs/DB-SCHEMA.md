# データ設計

## 概要

このシステムは、単一のリレーショナルデータベースを持たない。イベントの業務データは Notion、同期状態は Workers KV、競合しやすい小さな状態は Durable Object に保存する。

`workers/wrangler.jsonc` に Cloudflare D1 の `d1_databases` はない。`new_sqlite_classes` は `SyncCoordinator` Durable Object のマイグレーションであり、D1 スキーマではない。

## Notion イベントデータベース

既定のプロパティ:

| プロパティ | Notion 型 | 用途 |
| --- | --- | --- |
| `イベント名` | `title` | イベント名 |
| `内容` | `rich_text` | 説明 |
| `日時` | `date` | 開始・終了日時 |
| `場所` | `rich_text` | 開催場所 |
| `メッセージID` | `rich_text` | Discord 側識別子 |
| `作成者ID` | `rich_text` | Discord 作成者 |
| `ページID` | `rich_text` | 関連ページ識別子 |
| `イベントURL` | `url` | Discord などの参照 URL |
| `GoogleイベントID` | `rich_text` | Google Calendar 側識別子 |

`NOTION_EVENT_INTERNAL_ID` は内部向けデータベースを示す。`NOTION_EVENT_ID` が設定されている場合は、外部向けデータベースも同期対象になる。

プロパティ名は `NOTION_PROP_*` 環境変数で上書きできる。スキーマ変更時は、作成、更新、検索、削除の全経路で同じプロパティ名を使うことを確認する。

## Notion Q&A データベース

| プロパティ | Notion 型 | 用途 |
| --- | --- | --- |
| `質問` | `title` | 質問本文 |
| `回答` | `rich_text` | 回答 |
| `質問番号` | `number` | 表示順の連番 |

## Workers KV

| キー | 値の概略 | 用途 |
| --- | --- | --- |
| `sync:updated_min` | RFC3339 文字列 | Google 差分取得カーソル |
| `sync:last_epoch` | Epoch 秒 | Durable Object がない場合の最終同期時刻 |
| `map:gcal_discord` | JSON 対応表 | Google と Discord の ID 対応 |
| `map:gcal_notion` | JSON 対応表 | Google と Notion 内部・外部ページの対応 |
| `discord:snapshot` | JSON | Discord 差分検出用スナップショット |
| `sync:google_apply_queue` | JSON 配列 | Google 反映の繰り越し |
| `sync:discord_notion_queue` | JSON 配列 | Discord 同期の繰り越し |
| `google:access_token` | 文字列 | Google アクセストークン |
| `google:expires_at` | Epoch 秒 | Google トークン期限 |
| `gcal_watch_state` | JSON | Google watch の channel、resource、期限、Webhook token の SHA-256 fingerprint |
| `qa_cache` | JSON | Q&A 通知済み状態 |
| `reminder_cache` | JSON | リマインド送信済み状態 |
| `cleanup:last_epoch` | Epoch 秒 | クリーンアップ最終実行時刻 |
| `result:<処理名>` | JSON | 同期・ジョブの最新結果 |

Durable Object がない場合、`gcal_msg:<channel_id>:<message_number>` を KV の Webhook 重複抑止キーとして使用する。

KV の JSON は安定した文字列表現で保存し、同じ内容の不要な再書き込みを避ける。KV は最終的整合性であり、厳密な一意制約や複数キーのトランザクションを提供する前提ではない。

旧E2E実装の `e2e:google_calendar_crud`、`e2e:discord_crud`、`e2e:notion_crud` がKVに残っている場合、新しいE2E probeは外部操作前に停止する。値を応答へ出さず、既存資源のcleanup状態を人が確認して旧キーを処理するまで自動移行しない。

## Durable Object

`SYNC_COORDINATOR` の名前付きインスタンス `global` を使用する。

| ストレージキー | 値 | 用途 |
| --- | --- | --- |
| `lock` | 所有者と期限の JSON | 同期の排他 |
| `sync:last_epoch` | 最終時刻の JSON | クールダウン |
| `gcal_msg:<channel_id>:<message_number>` | 期限と任意のE2E所有run IDのJSON | Google Webhook 重複抑止 |
| `e2e:manifest:google` | E2E cleanup manifest の JSON | Google fixture の所有権と復旧 |
| `e2e:manifest:discord` | E2E cleanup manifest の JSON | Discord fixture の所有権と復旧 |
| `e2e:manifest:notion` | E2E cleanup manifest の JSON | Notion fixture の所有権と復旧 |
| `e2e:manifest:discord_google` | E2E cleanup manifest の JSON | Discord→Google scenario の両資源の所有権と復旧 |
| `e2e:manifest:discord_notion` | E2E cleanup manifest の JSON | Discord→Notion scenario の両資源の所有権と復旧 |
| `e2e:manifest:google_discord` | E2E cleanup manifest の JSON | Google→Discord scenario の両資源の所有権と復旧 |
| `e2e:manifest:google_notion` | E2E cleanup manifest の JSON | Google→Notion scenario の両資源の所有権と復旧 |
| `e2e:manifest:qa_notification` | E2E cleanup manifest の JSON | QA通知 scenario のNotion pageとDiscord messageの所有権と復旧 |
| `e2e:manifest:reminder` | E2E cleanup manifest の JSON | 前日リマインド scenario の Discord Scheduled Event と message の所有権と復旧 |
| `e2e:manifest:notion_cleanup` | E2E cleanup manifest の JSON | Notion期限cleanup scenario の期限到来・将来日時 page の所有権と復旧 |
| `e2e:manifest:webhook_dispatch` | E2E cleanup manifest の JSON | Webhook simulation scenario の Google event、Notion page、重複状態の所有権と復旧 |

E2E manifest は cleanup が必要な間だけ実 ID を保持し、clean 化後は SHA-256 fingerprint へ置き換える。`google_notion` と `webhook_dispatch` は Google event ID と Notion page ID、`google_discord` と `discord_google` は Google event ID と Discord Scheduled Event ID、`discord_notion` は Discord Scheduled Event ID と Notion page ID、`qa_notification` は Notion Q&A page ID と Discord message ID、`reminder` は Discord Scheduled Event ID と message ID、`notion_cleanup` は期限到来 page ID と将来日時 page ID を同じ run ID で保持し、片方でも cleanup または所有権確認に失敗すれば dirty を維持する。`webhook_dispatch` はさらにrun専用channel IDとmessage numberを保持し、同じrun IDが所有するDurable Object重複状態だけを削除する。run ID、service、kind、サイズを Durable Object 側でも検証し、`SYNC_COORDINATOR` がない場合は KV へフォールバックせず失敗させる。

Durable Object は高頻度かつ整合性が必要な状態に限定し、イベント本文や大きな対応表は KV または外部サービスへ置く。Workers KV は結果整合で同一キーの短時間連続更新にも不向きなため、E2E の cleanup 所有権には使わない。

## データの正本

- Google Calendar、Discord、Notion のどれを各属性の唯一の正本とするかは同期方向によって異なる。
- サービス間の ID 対応は KV と Notion プロパティで補助する。
- 削除は、Google の `cancelled`、Discord の消失、Notion のアーカイブとして各サービス固有の表現へ変換する。
- キューに残った項目は未処理または再試行対象であり、完了データとして扱わない。

## スキーマ変更の手順

1. 読み取り元と書き込み先の全モジュールを確認する。
2. 既存データとの互換性と移行方法を定義する。
3. `workers/wrangler.jsonc` のバインディングまたはマイグレーション要否を確認する。
4. `README.md`、この文書、必要なら詳細 KV 文書を更新する。
5. 静的検査に加え、許可された検証環境で作成・更新・削除・再試行を確認する。
