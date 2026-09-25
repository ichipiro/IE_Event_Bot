# 通常ジョブの失敗後再試行E2E

`deploy-and-jobs-retry-smoke` は専用E2E Workerを1回deployし、Q&A、リマインド、Notion cleanupの順に、通常HTTPハンドラ・共有KV・実サービス資源を検証する。各シナリオの回収後に次のシナリオへ進む。

## 検証対象

| ジョブ | 固定失敗 | 別HTTPの再試行と完了条件 |
| --- | --- | --- |
| Q&A | 3ページの採番・初回抑止・実更新後、未回答2件の最初の通知だけを失敗させる。通常ハンドラは500、通知は1件。失敗ページの古いcacheを維持する | 残り1件だけを実送信し計2件。先に成功したmessage IDを維持し、次の重複実行では増えない |
| リマインド | 4予定中の対象2件で最初の通知だけを失敗させる。通常ハンドラは500、通知・cacheは成功した1件のみ | 残り1件だけを実送信し計2件。範囲外2件を抑止し、重複実行ではmessage・cacheが増えない |
| Notion cleanup | 期限切れ・将来日時の2ページで、期限切れページのarchiveを失敗させる。通常ハンドラは500、両ページを保持し成功時刻は未保存 | 次HTTPで期限切れだけを実archiveし成功時刻を保存。その次はinterval guardで抑止する |

Q&Aは `prepare → first → update → fail → notify → duplicate`、ほかは `prepare → fail → notify/execute → duplicate`。各段階の後に別HTTPの `verify` を必要とする。Notion更新時刻の粒度を越えるため、Q&A更新前に65秒待つ。

## 所有権・証跡・回収

- 既存E2E入口の認証、run/version確認、DO排他、対象fingerprint、空の専用DB/Guild・共有キーの検査を使用する。
- 失敗注入は認証・所有権確認済みの要求が作るenv wrapper内の非公開callableだけに設定する。通常WorkerのHTTP入力や文字列環境変数では設定できず、モジュール関数や他の要求を変更しない。
- 注入は最初の書込みを実APIへ送る前に `False` を返す。他の送信と次HTTPの再試行は実APIを使用する。`*_failure_injected=200` は注入実行確認、`*_failed_http=500` は通常ハンドラの実応答であり、外部APIの503/500受信を意味しない。
- verifyは実サービスの内容・件数・IDと共有KVのdigest・値・結果を読み直す。失敗段階未検証のまま再試行できず、失敗段階の再実行も拒否する。
- Notionページ5件をarchive、Discord予定4件と通知計4件を削除し、通常名の共有KV計6キーを回収する。所有権を確認できない値は削除せずdirtyを維持する。
- 全3manifestの `outcome=passed`、`dirty=false`、失敗・再試行・重複・回収の各stageとrun/version/commit、監査、JUnitを独立照合する。中途失敗後に回収だけ完了した場合は `failed_clean` とする。

## 実装修正と境界

Q&Aは送信失敗時に更新前のcache値を維持し、新規ページならcacheへ追加しない。cleanupはarchive失敗時に成功時刻を進めない。2件の不具合を失敗するローカルテストで再現してから修正した。

今回の失敗は固定注入であり、Discord/Notionの実障害、POST受理後の応答喪失、回線断、Worker中断、KV書込み失敗、実Cron起動、一覧取得失敗、任意件数・ページ送り、KVの全リージョン一貫性を証明しない。通常Notion一覧取得の失敗処理はこのシナリオの対象外である。

関連: [全体計画](E2E-PLAN.md)、[通常cleanup](E2E-NOTION-CLEANUP-NORMAL.md)、[検証方法](TESTING.md)。

## 2026-09-25 実行結果

[実行36106153256](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36106153256)（commit `cbfa2d9`）で、Q&A・リマインド・Notion cleanupの固定失敗、通常HTTPの500、別HTTPの再試行、重複抑止、共有KVと全所有資源の回収を確認した。全3manifest `passed`・`dirty=false`、監査70行・35操作、37段階検証、run/version/commit一致、JUnit907件成功を独立照合済み。失敗は書込み前の固定注入であり、実サービス障害・応答喪失・実Cronは対象外。

run IDは `E2E-20260925T071039Z-e2335426`。証跡は `test-results/jobs-retry-36106153256/artifacts/`、独立照合は同ディレクトリの親の `verification.json` に保存した。Python907件、Node309件、Ruff・Pyright・設定/機密保護/workflow検査、Wrangler E2E dry-runが成功。本番Workerは未デプロイ。
