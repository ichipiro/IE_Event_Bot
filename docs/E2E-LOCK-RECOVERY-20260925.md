# ロック解放の自動復旧・実環境検証

2026-09-25、E2E専用Workerへのデプロイ、固定障害による解放復旧、通常watch維持、実Webhookから共有状態への同期、全所有資源の回収が成功した。

## 実行と照合

- Workflow: [36125191568](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36125191568)
- 実行モード: `deploy-and-watch-shared-smoke`
- コードcommit: `ef6bba7daf95590f30c1fb272076174f5e640029`
- run ID: `E2E-20260925T104141Z-d8ac349e`
- Worker: `ie-event-bot-e2e`
- Worker version SHA-256: `7ed00a10400cb6bce88abf5cdc14deb5e68b9d9170939cd27028add7194d4d83`
- GitHub Environment `e2e` の承認後に実行。CI checkoutのcommit一致・`dirty=false`、デプロイ監査と最終HTTP読戻しのversion一致、既存専用資源のfingerprint一致を独立照合した。
- Python 1,031件、Node 323件、Ruff、Pyright、設定・機密情報保護検査、Wrangler dry-runが成功した。

## 解放復旧の8ケース

通常同期の解放処理とGoogle同期E2Eの制御ロック解放処理を、run専用の実Durable Objectへ接続した。
実RPCの前後に固定例外を注入し、新しい接続から状態を読み戻した。
表の回数は初回を含む解放呼出し回数。障害注入で実DOへ送らない呼出しも含む。

| 条件 | 通常同期 | E2E制御 | 確認内容 |
| --- | --- | --- | --- |
| 解放前の失敗 | 成功 | 成功 | 初回失敗後に1回再送。状態確認2回で解放済みを確認 |
| 解放後の応答喪失 | 成功 | 成功 | 状態確認1回で解放済みと判定。解放の再送なし |
| 恒久的な解放失敗 | 成功 | 成功 | 初回＋再送2回で打切り。状態確認3回、ロック残留と失敗ログを確認後、所有ロックを回収 |
| 所有者交代 | 成功 | 成功 | 初回失敗後の確認で別ownerを検出。解放を再送せず、交代後のロックを維持 |

8件の `watch_shared_release_*` と全体の `watch_shared_release_recovery` はすべて200。
ケース内の上限到達は期待した結果であり、E2E全体の失敗ではない。

## ログと通常同期

run開始から証跡収集までの専用Workerのログを取得し、ページ取得の完了を確認した。
任意のログ本文・例外本文は成果物へ保存せず、固定分類だけを保存した。

| 分類 | 件数 |
| --- | ---: |
| 初回解放失敗 | 4 |
| 復旧成功 | 10（検証ケース6、検証後の所有ロック回収4） |
| 再試行後も解放できない | 2 |
| 障害注入ケース成功 | 8 |

通常watchの登録・有効時の維持・期限接近時更新・期限情報欠落時更新・token変更時更新・再登録が成功。
Googleの実変更通知を起点とするAlarm・通常同期・共有状態読戻しを3段階で確認し、各段階の重複抑止も確認した。
実行中ロックとGoogle API認証拒否を使う既存の再試行検証も成功した。

## 回収と証跡

- `google_sync.outcome=passed`、全scenarioの `dirty=false`、最終watch不在。
- watch、所有Google予定、Notionページ、Discord予定、共有KV、通知キュー／Alarmを回収。
- run専用の検証用DOロックも回収・読戻し済み。`watch_shared_release_recovery_cleanup=200`。
- 監査78行・39操作を照合。ポーリング中の `google_sync_busy` / `google_sync_not_ready` は想定内。
- GitHub成果物: `e2e-evidence-36125191568-1`、`release-recovery-logs-36125191568-1`、`pytest-junit-36125191568-1`。
- ローカルの秘匿済み証跡: `test-results/lock-recovery-36125191568/`。独立照合結果は同ディレクトリの `verification.json`。

この結果は専用環境での固定障害注入による復旧を証明する。実際のCloudflare障害を発生させた検証、本番Workerへのデプロイ、過去の `JsException` の根本原因特定は含まない。
実装上の条件は [BACKEND.md](BACKEND.md#ロック解放失敗の記録と復旧) を参照する。
