# データ設計

## 概要

E2Eの通常KV検証では `e2e:discord_state:<run_id>:<scope_id>:discord:snapshot` と同prefixの `sync:discord_notion_queue` だけを使用する。scopeは準備時に生成する32桁hex、run IDと所有event ID一覧・対象fingerprintはDOの `e2e:manifest:discord_state` に保持する。DOはdirtyな所有権の差し替えとclean後の同run再利用を拒否する。回収後はrun IDとfingerprint、結果だけを残す。通常のKVキーと既存Discord差分checkpointは変更しない。

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

`sync:discord_notion_queue` の各要素は `id` と `op`（`upsert` / `delete` / `notify`）を持つ。新規イベントの通知が未完了なら `notification: {channel_id, message_id?}` を付加する。`message_id` は投稿結果を取得した後だけ記録し、リアクションの再試行に使う。同期未完了は `upsert`、同期成功後の通知未完了は `notify` として保存する。完了後はqueueから取り除く。通知なしの従来の `upsert` / `delete` 要素も読み込めるが、旧queueに失われた通知の要否は復元できない。旧実装は `notify` を理解しないため、保留通知がある状態での旧版への切戻しは再同期・通知消失の可能性がある。

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
| `e2e:manifest:webhook_delivery` | E2E cleanup manifest の JSON | 短命なGoogle watch channel、resource ID、初回sync通知の所有権と復旧 |
| `e2e:manifest:webhook_change` | E2E cleanup manifest の JSON | Google event更新、短命watch、最初のexists通知、Notion page、重複状態の所有権と復旧 |

E2E manifest は cleanup が必要な間だけ実 ID を保持し、clean 化後は SHA-256 fingerprint へ置き換える。`google_notion`、`webhook_dispatch`、`webhook_change` は Google event ID と Notion page ID、`google_discord` と `discord_google` は Google event ID と Discord Scheduled Event ID、`discord_notion` は Discord Scheduled Event ID と Notion page ID、`qa_notification` は Notion Q&A page ID と Discord message ID、`reminder` は Discord Scheduled Event ID と message ID、`notion_cleanup` は期限到来 page ID と将来日時 page ID を同じ run ID で保持し、片方でも cleanup または所有権確認に失敗すれば dirty を維持する。`webhook_dispatch` はさらにrun専用channel IDとmessage numberを保持し、同じrun IDが所有するDurable Object重複状態だけを削除する。`webhook_delivery`は外部request前にrun専用channel IDを保持し、watch応答と初回通知のどちらが先でも同一resource IDだけを原子的に紐付ける。`webhook_change`はさらにevent更新前cursor、最初の`exists` message number、dispatch要約を保持し、後続の`exists`通知は新たなdispatchへ進めない。watch停止とrun所有重複状態の削除が成功してからeventとpageを回収し、channel / resource / message numberと外部資源IDをfingerprintへ置き換える。run ID、service、kind、サイズを Durable Object 側でも検証し、`SYNC_COORDINATOR` がない場合は KV へフォールバックせず失敗させる。

Durable Object は高頻度かつ整合性が必要な状態に限定し、イベント本文や大きな対応表は KV または外部サービスへ置く。Workers KV は結果整合で同一キーの短時間連続更新にも不向きなため、E2E の cleanup 所有権には使わない。

## データの正本

- Google Calendar、Discord、Notion のどれを各属性の唯一の正本とするかは同期方向によって異なる。
- サービス間の ID 対応は KV と Notion プロパティで補助する。
- 削除は、Google の `cancelled`、Discord の消失、Notion のアーカイブとして各サービス固有の表現へ変換する。
- キューに残った項目は未処理または再試行対象であり、完了データとして扱わない。

## 通常Google同期E2Eの状態

通常Google同期E2Eの `google_sync` はDOの `e2e:manifest:google_sync` にrun・scope・対象fingerprint、2件の固定Google ID・作成着手・発見済みの下流ID、生成fixture、段階、cursor期待値、KV hashを保持する。KVは `e2e:google_sync:<run_id>:<scope_id>:` 配下の `sync:updated_min`、`map:gcal_notion`、`map:gcal_discord`、`sync:google_apply_queue`、`sync:last_epoch`、`result:sync_all` の6キーだけを許可する。通常StateStoreの形式を保持し、通常キーへの書込みと認証cacheを隔離する。DOはrun・対象・固定IDの差替え、着手済みflagの巻戻し、検証前のadvance、clean後の同run再実行を拒否する。clean後はfixture・実ID・cursor・状態hashを削除し、対象fingerprintと結果を保持する。`e2e:google-sync-control` は既存クラスの別名で、binding・migrationの追加はない。

## スキーマ変更の手順

1. 読み取り元と書き込み先の全モジュールを確認する。
2. 既存データとの互換性と移行方法を定義する。
3. `workers/wrangler.jsonc` のバインディングまたはマイグレーション要否を確認する。
4. `README.md`、この文書、必要なら詳細 KV 文書を更新する。
5. 静的検査に加え、許可された検証環境で作成・更新・削除・再試行を確認する。

`discord_kv` manifestは外部Discord event・Notion pageのIDとrun・scope・対象fingerprintを保持する。ID確定後の差し替えとscope変更はDOで拒否する。通常StateStoreは `e2e:discord_kv:<run_id>:<scope_id>:` の固定2キーへ書き込み、DOへsnapshot / queueを複製しない。外部回収後のKV削除が失敗した場合もdirtyと所有情報を保持する。

`discord_batch` manifestは固定2組のrun marker・外部ID・初期内容fingerprint・回収完了フラグを所有する。確定済みID・scopeの変更と回収完了の巻戻しをDOで拒否する。snapshot / queueは `e2e:discord_batch:<run_id>:<scope_id>:` の固定2キーだけに保存し、DOには複製しない。外部資源ごとに回収完了を記録し、全外部資源とKVの回収後にIDを除いた集約fingerprintへ置き換える。

`discord_batch_google` は別のDO manifestと `e2e:discord_batch_google:<run_id>:<scope_id>:` の固定2KVキーを使う。対象fingerprintにCalendarを加え、各fixtureへrun由来の `google_event_id`、Google作成着手、Google回収完了を保存する。通常の `discord_batch` とは所有manifest・KV prefixを共有しない。tokenやsnapshot / queueをDO所有manifestへ保存しない。


`discord_batch_notification` は `e2e:manifest:discord_batch_notification` と `e2e:discord_batch_notification:<run_id>:<scope_id>:` の固定2KVキーを使う。対象fingerprintへchannelとroleを追加する。各fixtureは `create_attempted.message`、`message_content_sha256`、取得後の `message_id`、`reaction_deferred`、`reaction_done`、`message_cleanup_done` を保持する。DOは取得済みID・本文hash・通知先の差し替えと完了フラグの巻戻しを拒否し、未回収messageがあるclean化を拒否する。KV adapterは所有event ID・通知先hash・DO記録済みmessage IDだけを受け入れる。snapshot / queueをDOへ複製せず、clean後はmessage IDもfixture fingerprintへまとめる。


`sync_lock` はDOの `e2e:manifest:sync_lock` にrun・scope・接続対象fingerprint・段階・6 roundの結果hashを保持する。KVは `e2e:sync_lock:<run_id>:<scope_id>:<round>:` 配下の `result:sync_discord_notion`、`result:sync_all`、`sync:last_epoch` だけを許可する。DOは所有情報と既存hashの差し替え、段階の巻戻し、clean後の同run再利用を拒否する。回収時は6×3キーを列挙なしで削除し、clean後はhash一覧とscope実値を取り除く。`e2e:sync-lock-control` という別名の既存 `SyncCoordinator` オブジェクトはE2E制御ロックだけを保持し、終了時に所有ownerを指定して解放する。binding・migrationの追加はない。


`sync_faults` はDOの `e2e:manifest:sync_faults` にrun・scope・対象fingerprint・段階・8ケースの証拠hashを保持する。KVは `e2e:sync_faults:<run_id>:<scope_id>:<case>:` 配下で `discord:snapshot`、`sync:discord_notion_queue`、`result:sync_discord_notion`、`result:sync_all`、`sync:last_epoch`、`evidence` の6キーだけを許可する。証拠には固定の適用回数・注入回数・残件回復結果・状態hashを保存する。DOは所有者・既存hashの変更とclean後の同run再利用を拒否する。1ケースの着手を `fault_testing`、確定を `fault_partial`（途中）または `fault_prepared`（全8件完了）に記録し、hashは固定順に1件ずつだけ追加できる。途中失敗した `fault_testing` からの再実行は拒否する。cleanupは8×6候補を列挙せず削除し、clean後はscope実値とhash一覧を除く。制御DO `e2e:sync-fault-control` は既存クラスの別名オブジェクトであり、binding・migrationを増やさない。


通常Discord snapshotの値は従来どおりevent IDからJSON文字列への辞書である。残件のある指紋JSONだけに `_pending_sync: {op, id, notification?}` を追加し、queueと同じ未処理操作を保持する。`notification` は通知先と投稿済みmessage IDを含む。削除待ちでは対象IDの記録を保持し、成功後に除く。DOへ通常queue本体を移さず、KVとDOの役割と既存bindingは維持する。E2Eの各KV adapterとdelta checkpointは、指紋内の残件についても許可ID・操作・通知先を検査する。
