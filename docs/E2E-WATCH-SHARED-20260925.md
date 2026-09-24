# 通常watch・共有Webhook E2Eの実行記録

## 結果

2026-09-25（JST）に専用E2E環境で実行した。通常watch維持は成功したが、実変更通知からの共有状態同期は完了せず、E2E全体は失敗した。

| 項目 | 結果 |
| --- | --- |
| 通常watch登録・有効時の維持 | 成功 |
| 期限しきい値による更新・期限欠損時の更新 | 成功 |
| token変更による更新・token復元 | 成功 |
| watch停止後の再登録・初回syncの受信 | 成功 |
| 実exists通知からの共有状態同期 | 制御ロックが残留し、最初の同期の完了確認に到達せず失敗 |
| 同期ロック取得中の通知を同番号で再送 | 同期されない現行動作を検出 |
| Google取得失敗後、有効な認証に戻して同番号で再送 | 同期されない現行動作を検出 |
| 回収 | 完了。`failed_clean`、全manifest `dirty=false`、watch状態なし、共有KV回収成功 |

再送2ケースは所有channelの別通知番号を内部生成し、通常Webhook入口を呼ぶ検証である。Google自身による同一通知の再配信を観測したものではない。Google取得失敗は固定の無効bearerで起こす。自然な障害や自動復旧成功を意味しない。

## 証跡

- 実行: [36020962563](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36020962563)
- run ID: `E2E-20260924T153518Z-cea727a5`
- 実行commit: `3655d3d79d45e7ce272cd333b389f4bcd5205bd4`
- artifact: `e2e-evidence-36020962563-1`、`pytest-junit-36020962563-1`
- run ID、Worker version fingerprint、実行commitを照合した。
- JUnitは802件成功。ローカルNodeテストは287件成功。外部動作の成否とは別の結果である。
- `watch_shared_maintenance=200`、`watch_shared_step_0=200`。
- `watch_shared_busy_retry_lost=409`、`watch_shared_failure_retry_lost=409`。
- `watch_shared_callback_1` は保存されていない。verifyと自動cleanupは `google_sync_busy` となった。
- 監査JSONLは74行・37操作。実通知の正常同期3回と往復確認は未達。

## 解釈と未解決事項

通常Webhookは同期dispatchより先に重複通知を記録する。実行中の通知は204、Google取得失敗は500になるが、どちらも同じ通知番号の再送が重複として204になり、同期は再実行されない。再試行欠落の修正はこの実行に含めていない。

実callbackの同期中断原因は未確定である。接続切断による中断を疑い、E2Eの事前確認と異常系検証を管理HTTPへ移し、callbackには `waitUntil` と `asyncio.shield` を接続したが、再実行でも完了しなかった。これを通常Workerの正常動作や復旧成功として扱わない。通常Worker側の同期処理は変更していない。

旧channelの拒否はE2E所有権ガードのローカル検証であり、通常Workerでの旧通知拒否の保証ではない。自然なwatch期限切れ、実Cron、本番環境は未検証。詳細な検証構成は [TESTING.md](TESTING.md#通常watchと共有状態を使う実webhook同期) を参照。

## 実行中に修正したE2E基盤

- MCP呼出しの既定60秒タイムアウトを、既存deployとrevision確認の待機上限に合わせた。
- 回収workflowが所有manifestの `working` 状態を拒否する問題を修正した。
- 期限切れロックを所有者名だけで実行中と判定する問題を修正した。DOの通常acquireで期限を判定し、自分が取得したロックだけを解放する。有効な他のロックは解放しない。
- 同じrun IDで再deployするときは、更新前と異なるWorker version fingerprintを確認する。
- Google同期の回収manifestを、別シナリオの残存として誤認する再deploy判定を修正した。

前の失敗run `E2E-20260924T151031Z-e59132f4` は [回収実行36020519163](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36020519163) で `failed_clean`、全manifest `dirty=false`、watch状態なし、共有KV回収成功を確認した。

最終runの[回収実行36021808092](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36021808092)はwatch停止後に `google_sync_release_failed` となった。安全な診断は `step=release_rpc`、`exception=js_exception`、応答判定は未取得だった。直後は制御ロックが残り `google_sync_busy`、manifestは `cleanup`・`dirty=true`、共有KV回収完了の記録はなかった。この診断は回収時の解放失敗位置を示し、実callbackの中断原因を確定するものではない。

ロックTTL経過後の[再回収36022380915](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36022380915)は成功した。artifact `e2e-evidence-36022380915-1` を独立照合し、同じrun ID・commit、更新された稼働version、`failed_clean`、全サービス・シナリオmanifest `dirty=false`、watch状態なしを確認した。`watch_shared_cleanup=200`、`google_sync_shared_cleanup=200`、cleanup全体200、Google予定の削除済み410、Discord予定の削除済み404、Notionページの回収時読戻し200を確認した。Notionは所有ページのアーカイブを回収とし、Googleの削除履歴とglobal DOの最終成功時刻は残す。
