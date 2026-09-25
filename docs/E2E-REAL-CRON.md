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
