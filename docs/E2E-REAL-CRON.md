# Cloudflare実Cron起動E2E

`E2E Staging` の `deploy-and-real-cron-smoke` は、GitHub Environment `e2e` の承認後、runごとに一時Python Workerを作る。既存の本番Workerと `ie-event-bot-e2e` は変更しない。

## 検証範囲

- `* * * * *` の実Cron配信を待ち、異なる2つの `scheduledTime` を確認する。HTTPからの手動起動経路はない。
- Pythonの `scheduled` から通常アプリケーションの `Application.scheduled` を呼ぶ。全ジョブフラグを明示的に無効にし、戻り値が空であることを確認する。
- 専用KV namespaceの `e2e:real-cron:<run ID>:<scheduledTime>` に、run・version ID/tag・commit・Cron式・予定時刻・受信時刻を保存する。
- runnerがCloudflare管理APIでschedule・100% deployment・version tagを確認し、別経路でKVを読む。任意の応答本文や秘密値は証跡へ出力しない。
- 2件の読戻し成功後、scheduleを空にして確認し、一時Workerを削除して404を確認する。進行中の短いハンドラを考慮して70秒待ち、所有KVだけを削除し、一覧と各値の欠損を確認する。`always()` でも回収を再確認する。

実行期間はrun開始から最大20分。Workerも同じ期限外の書込みを拒否する。Cron設定変更は[公式仕様](https://developers.cloudflare.com/workers/configuration/cron-triggers/)で最大15分の伝播時間があるため、配信待ちを含む。runnerの中断時はworkflowの `always()` がmanifestから回収する。workflow自体の強制終了などで回収できなければ、artifactの所有情報を使う運用上の回収が必要になる。期限ガードだけではCron登録やKVは消えない。

## 成功条件と限界

manifestの `outcome=passed`、`dirty=false`、異なる2件以上のreceipt、schedule空・Worker欠損・所有KV欠損をすべて必要とする。配信失敗後に回収だけ成功した状態は `failed_clean` とする。

この試験はCloudflareからPythonハンドラへの実起動を証明する。通常同期・通知・cleanupのCron実行、手動実行との競合、本番Worker、定刻配信の保証、KVの全拠点削除伝播は対象外。[E2E計画](E2E-PLAN.md)の実Cron項目のうち、手動実行との競合は別途検証する。

## ローカル確認

```bash
source .venv/bin/activate
pytest -q tests/test_e2e_real_cron.py tests/test_cron_workflow_policy.py
node --test tools/run_cron_e2e.test.mjs
python tools/validate_e2e_workflow.py
npm run wrangler -- deploy --dry-run --config workers/wrangler.cron-e2e.jsonc
```

ローカル試験では期限・run/version・Cron式・通常ジョブフラグ・HTTP拒否・KV失敗・所有外Workerの拒否・回収失敗・workflowの承認境界を検証する。これらは実Cronの配信証跡ではない。

## 読取り専用診断

`read-only-real-cron-diagnostics` と `recovery_run_id` に対象run IDを指定すると、その一時Workerの作成時刻から23分間のWorkers Logsを照会する。deploy・schedule変更・KV書込み・cleanupは実行しない。時刻・event type・outcome・既知のエラー分類だけを保存し、任意のログ本文は保存しない。Telemetryの `scheduledTime` はcontrollerのミリ秒値と単位が異なる場合があるため、`scheduled_time_raw` として記録する。

## 初回失敗と原因の確認（2026-09-25）

[実行36097156714](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36097156714)（commit `c04fa0a`）は20分で `cron_delivery_timeout` となり、receiptは0件だった。schedule空・Worker削除後404・所有KV欠損、監査78行、`failed_clean`・`dirty=false`、JUnit888件成功を独立照合した。実行期間は正しく20分であり、別途修正した時計の2回読取りによる期限ずれは、この失敗の原因ではなかった。

[読取り専用診断36099380804](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36099380804)で、同Workerへの `scheduled / ok` を18回、05:10:17.596〜05:27:16.155 UTCに確認した。Telemetryの予定時刻は秒単位で、すべて毎分16秒だった。検証ハンドラが予定時刻に課していた `scheduled % 60000 == 0` が誤った制約だったため除去し、分境界以外の予定時刻で記録・読戻し・回収を検証するローカルテストを追加した。

初回の証跡と独立照合結果は `test-results/real-cron-36097156714/`、診断の照合結果は `test-results/real-cron-diagnostic-36099380804/verification.json` に保存する。旧診断artifactの `scheduled_time_ms` はTelemetryの元値を保持したフィールド名の誤りであり、秒として独立照合済み。以後は `scheduled_time_raw` に修正した。

## 修正後の成功（2026-09-25）

[実行36099565944](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36099565944)（commit `b4e9cf3b5a8f80ee61e21899178d831eb869c925`）で実Cron起動からKV読戻し・回収まで成功した。

| 確認項目 | 実測結果 |
| --- | --- |
| run ID | `E2E-20260925T054209Z-bf496419` |
| Worker version | `c26760ba-1586-4de9-875c-82a9ffb52402` |
| Cron予定時刻（JST） | 14:45:11、14:46:11 |
| ハンドラでの観測遅延 | 1,036 ms、17,806 ms |
| 別経路でのKV読戻し | 2件。run・version ID/tag・commit・Cron式が一致 |
| 回収 | schedule空、Worker削除後404、所有KV2キー削除後の欠損を確認 |
| 最終状態 | `passed`・`dirty=false` |
| 証跡 | 監査47行、JUnit888件成功、workflow内と `always()` の回収が成功 |

証跡は `test-results/real-cron-36099565944/artifacts/`、独立照合は同runディレクトリの `verification.json` に保存した。GitHub側ではRuff・Pyright・Python888件・既存Node305件・Cron専用Node10件・設定検査・既存E2E dry-runが成功した。専用Cron設定のWrangler dry-runもローカルで成功済み。これは全ジョブ無効の通常scheduled dispatchまでの実起動確認であり、各通常ジョブのCron実処理や手動同期との競合は未検証である。
