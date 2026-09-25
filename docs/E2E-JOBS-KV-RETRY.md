# 通常ジョブのKV保存失敗と再試行

`deploy-and-jobs-kv-retry-smoke` は専用E2E Workerを1回deployし、Q&A、リマインド、Notion cleanupの通常HTTP処理・共有KV・別HTTP検証・回収を順に実行する。

## 修正

通常ジョブの6キーに限り、`StateStore.put_text` が同じ値の保存を最大3回試す。失敗後は1秒、2秒待つ。外部通知・archive・ジョブ全体はこの再試行で呼び直さない。値の比較と結果timestampの生成は再試行の外で行い、保存直後の例外でも同じ値を使う。

対象は `qa_cache`、`reminder_cache`、`cleanup:last_epoch`、`result:job_qa_check`、`result:job_reminder`、`result:job_cleanup`。同期queue・snapshotなどの保存は従来どおり。キャンセルは再試行しない。

3回とも失敗したら、通常HTTPは `ok=false`・`error=job_kv_write_failed` の500を返す。KV例外の本文は返さない。Cronも失敗として集計し、後続ジョブへ進む。cache保存に失敗した場合は可能なら失敗結果を保存し、結果自体の保存が失敗した場合は成功と報告しない。

## E2E

通常モードの `prepare` を認証付きの `kv_prepare` に替える。Q&Aは初回採番・抑止・実更新を経てnotify時、ほかはnotify／execute時に、cache／成功時刻と結果の両キーへ次の注入を行う。

1. 初回は所有権検査後、実KV保存前に固定例外を返す。
2. 2回目は所有digestをDOに記録し、実KVへ保存してから固定例外を返す。
3. 3回目は同じ値を実KVへ保存し、正常終了する。

注入wrapperはその要求のenvにのみ渡す。通常HTTPの入力や文字列環境変数からは有効化できない。各キーの3回・値の同一性を検査し、`*_kv_cache/result_before/after/recovered=200` を記録する。これらは固定注入と回復の確認であり、Cloudflareの障害statusではない。

| ジョブ | 回復後・次HTTPの検証 |
| --- | --- |
| Q&A | 未回答2件の通知・全3ページのcache・結果を読戻す。回答済み通知を抑止し、次HTTPで通知IDが増えない |
| リマインド | 4予定中2件だけ通知し、対象cache・結果を読戻す。次HTTPで通知IDとcacheが変わらない |
| cleanup | 期限切れ1件をarchiveし将来日時1件を保持する。成功時刻・結果を読戻し、次HTTPはinterval guardで抑止する |

各段階の別HTTP verifyを必須とする。所有Notion5ページをarchive、Discord予定4件・通知4件を削除し、共有KV6キーを回収する。固定失敗の証跡が欠けた場合や途中停止後の回収は `failed_clean` とする。

## 保証の境界

保存失敗が回数制限内に回復した場合の検証である。再試行を使い切ってcacheが未保存のまま次のジョブが走ると、通知は再送され得る。結果保存だけの失敗でcacheが保存済みなら、次ジョブの重複抑止はcacheによる。

外部処理とKVは原子的ではなく、一度限りの通知は保証しない。並行ジョブによる上書き競合、古いKV読取り、実サービス障害、Worker中断、実際の応答喪失、実Cron配信はこのE2Eの対象外。保存直後の固定例外は実際の通信障害の観測ではない。STATE_KVとSYNC_COORDINATORの責務・binding・Cron設定は変更しない。

関連: [通常ジョブの外部書込み失敗](E2E-JOBS-RETRY.md)、[一覧取得失敗](E2E-JOBS-LIST-RETRY.md)、[全体計画](E2E-PLAN.md)、[検証方法](TESTING.md)。
