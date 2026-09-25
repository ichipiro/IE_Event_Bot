# 実Cronと手動同期の競合E2E

`E2E Staging` の `deploy-and-real-cron-contention` を使用する。GitHub Environment `e2e` の承認後、run専用の一時Python Workerを作り、実Cronと別HTTPリクエストの両方向の競合を検証する。

## 経路と成功条件

- 実Cron → `Application.scheduled` → `_run_discord_sync` と、認証済み `POST /sync/discord-notion` → `Application.fetch` → 同じ `_run_discord_sync` を使用する。
- 同期本体だけを待機用runnerに差し替え、実Cron側35秒・手動側85秒の保持で競合を作る。Discord・Notion・Googleへの取得・書込み・通知は行わない。
- 実Cron保持中の手動要求は409、同期本体0回・結果KV書込み0回、前後のowner一致を必要とする。
- 手動保持中の実Cronも409、同期本体0回・結果KV書込み0回、前後のowner一致を必要とする。実Cronの証跡をKVから独立に読む。
- 保持側の成功、finallyでのロック解放、次の手動同期の成功、通常StateStoreが保存した結果の管理API読戻しを確認する。
- run・version ID/tag・commitを全応答で照合する。競合直前にownerが変わった場合は412として成功件数に含めない。

## 所有範囲と回収

Worker名・KV prefix・20分の期限は[配信E2E](E2E-REAL-CRON.md)と同じ契約を使う。DOは既存E2E Workerの `SyncCoordinator` に接続するが、`global` ではなく `e2e:real-cron:<run ID>` に隔離する。既存Workerのコードと設定は変更しない。結果キーは同prefixの `result` に限定する。

HTTPはEnvironmentの既存認証を使用する。秘密値・任意のHTTP応答本文をartifactへ保存しない。schedule空、DOロック解放、一時Worker削除後404、所有KVの一覧と値の欠損を確認してから `passed`・`dirty=false` とする。失敗後の回収成功は `failed_clean`。`always()` も同じmanifestで回収する。

## 検証境界

通常の単独Discord同期と同じロック・結果保存・解放処理を、実Cronと別HTTPから呼ぶ試験である。外部API適用中の競合、通常 `/sync/all` の実Cron競合、TTL超過、実サービス障害、本番Worker、KVの全拠点削除伝播は対象外。

ローカルでは両方向の競合、結果保護、解放、認証・期限・version・所有者拒否、KV失敗時の解放、証跡改変拒否、回収時の強制解放禁止を検証する。実Cron配信は実サービス証跡で別途判定する。

## 実サービスの結果（2026-09-25）

[実行36104059809](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36104059809)（commit `3bfc782b9a2f49ca12fea86801bc56ab107f6189`）で成功した。

| 項目 | 確認結果 |
| --- | --- |
| run ID | `E2E-20260925T064401Z-ef831bbc` |
| Worker version | `2db7b3e7-4a6c-40ad-8e9e-45affb4ae4e1` |
| 実Cron予定時刻（JST） | 15:48:06、15:49:06、15:50:06 |
| Cron保持中の手動同期 | HTTP 409、本体0回・結果保存0回、保持owner一致 |
| 手動保持中の実Cron | 409、本体0回・結果保存0回、手動保持側のownerと一致 |
| 保持終了後 | ロック解放、手動同期200、本体1回・結果保存1回 |
| 結果KV | 管理APIで `after` の結果とrun・version・commitを読戻し |
| 回収 | schedule空、DOロック欠損、一時Worker削除後404、所有KV4キーの欠損 |
| 最終状態 | `passed`・`dirty=false`、workflow内と `always()` の回収成功 |
| 検証 | 監査84行、JUnit899件成功、既存Node305件・Cron用Node13件成功 |

artifactの全receipt、両方向の拒否と保持側owner、結果読戻し、回収、workflow SHA、JUnitを独立照合した。証跡は `test-results/cron-contention-36104059809/artifacts/`、照合結果は同runディレクトリの `verification.json` に保存した。Ruff・Pyright・設定検査・Wrangler dry-runも成功済み。
