# 通常ジョブのNotion一覧取得失敗と再試行

`deploy-and-jobs-list-retry-smoke` は、専用E2E WorkerでQ&AとNotion cleanupの通常HTTP処理・共有KVを検証する。リマインドはNotion一覧を使わないため対象に含めない。

## 修正内容

`jobs._notion_query_all_pages` は、HTTP失敗・通信例外・不正JSON・不正一覧形式・欠落／循環cursorを取得失敗として返す。途中までの一覧を採番・通知・archiveへ渡さない。Q&Aとcleanupは安全な固定エラーコードを含む失敗結果を返し、通常HTTP入口は500と結果KVを保存する。Q&A cacheとcleanup最終成功時刻は保持し、次のHTTPまたはCron呼出しで先頭から再取得する。同じHTTP内での自動再試行は追加しない。

未設定DBによる従来のskipは維持する。設定済みDBのNotion token欠落を正常な空一覧として扱わない。正常な空一覧は引き続き成功である。

## 実サービスでの検証構成

| 段階 | 失敗注入と保持する状態 | 次HTTPの回復 |
| --- | --- | --- |
| Q&A `list_fail_first` | 採番用のqueryをpage_size=1で実送信し、2ページ目に固定503を返す。質問番号・ページ内容を保持し、cacheは未作成、通知0件 | 3件全件を再取得して41/42/43を採番。初回通知を抑止 |
| Q&A `list_fail` | 実更新後、採番用queryは完走させ、通知用queryの2ページ目に固定503を返す。更新前cacheとページ内容を保持し、通知0件 | 未回答2件だけを通知。別HTTPで重複抑止 |
| cleanup `list_fail` | page_size=1の実query後、2ページ目に固定503を返す。期限切れ・将来日時の両ページを保持し、成功時刻は未保存 | 期限切れ1件だけarchive。成功時刻を保存し、次HTTPはinterval guardで抑止 |

Q&Aは `prepare → list_fail_first → first → update → list_fail → notify → duplicate`、cleanupは `prepare → list_fail → execute → duplicate`。各段階の後に別HTTPのverifyで実サービスと共有KVを読み直す。失敗段階が未検証なら再試行しない。Q&A更新前は時刻粒度を越えるため65秒待つ。

## 所有権と証跡

認証、run/version、DO排他、専用DBの空状態、対象fingerprint、共有キーの空状態を確認する。注入callableは認証・所有確認後に作る要求専用env wrapperにのみ設定し、通常HTTP入力や文字列環境変数からは設定できない。moduleのfetchや他要求のenvを置換しない。

`*_first_page=200` は実Notion応答と継続cursorの確認、`*_injected=503` は固定応答の注入、`*_http=500` は通常ハンドラの結果である。実Notionが503を返した証拠ではない。

Notion所有5ページをarchive、Discord通知2件を削除し、`qa_cache`・`result:job_qa_check`・`cleanup:last_epoch`・`result:job_cleanup` を回収する。所有外の状態は削除しない。両manifestの `passed`・`dirty=false`、run/version/commit、各stage、監査、JUnitを独立照合する。途中停止後の回収は `failed_clean` とする。

## 検証の境界

ローカルでは初回・2ページ目の失敗、Q&A採番／通知の両query、cleanup、不正応答・cursor循環・通信例外・token欠落、空一覧、再試行・重複抑止と途中回収を検証する。実サービスでは所有3件／2件の少数ページ送り後の固定503を使う。Notionの実障害、応答喪失、実Cron起動、任意件数、KV保存失敗、全リージョン一貫性の証明には含めない。

関連: [書込み失敗の再試行](E2E-JOBS-RETRY.md)、[全体計画](E2E-PLAN.md)、[検証方法](TESTING.md)。
