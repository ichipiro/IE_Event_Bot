# 通常watch・共有Webhook E2Eの実行記録

修正後の実行 [36027225893](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36027225893) はworkflow全体が成功した。実Google通知3回の共有同期・往復、固定失敗後の同番号再試行、全所有資源・共有KV・通知キュー・Alarmの回収をartifactで確認済み。その後の再確認36117345631は準備後の制御ロック解放で失敗した。その後、自動復旧実装を含む[実行36125191568](E2E-LOCK-RECOVERY-20260925.md)で全体が再び成功した。失敗履歴は以下に保持する。旧通知の通常handler検証は[棚卸し記録](E2E-AUDIT-20260925.md)で別途追跡する。本番Workerは未デプロイ。

## 修正前の結果

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

## 修正と再実行

修正依頼を受け、失敗時間帯の限定ログを[診断36024929673](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36024929673)で取得した。共有Webhook時間帯216件、回収時間帯88件を固定分類だけで保存した。前者には `canceled` のHTTP実行3件（wall time 15.301秒、38.189秒、15.459秒）があり、後者にはなかった。元の例外本文や秘密値は記録していない。送信元の切断理由や回収時のRPC例外の詳細原因は、この分類ログでは確定できない。

`7ce438e` では通常Webhookの通知をDOへ保存してから受理し、Alarmから通常同期を呼ぶようにした。HTTP接続終了後の `waitUntil` 延長は最大30秒のため、長い同期の完了保証には使わない。Alarmは受信HTTPの接続寿命に依存しない。[Cloudflareの実行時間制限](https://developers.cloudflare.com/workers/platform/limits/#duration)と[Alarm仕様](https://developers.cloudflare.com/durable-objects/api/alarms/)を参照。

通知単位の処理中leaseと成功済み記録も分離した。同期失敗・実行中・クールダウンでは成功済みにせず、同番号の再送を処理できる。成功後の同番号通知は再実行しない。Alarmの未完了通知は永続キューに保持し、再実行を予約する。旧KV専用構成と既存の処理済み記録の読取り互換を維持する。これにより、過去に旧版が処理済みとして記録した通知の成否を復元できるわけではない。

再現テストは修正前に失敗を確認し、修正後に成功した。Python全体816件、Node290件、Ruff、Pyright、E2E設定・secret hygiene・workflow検査、通常版とE2E版のWrangler dry-runが成功した。実環境結果とは分けて扱う。

修正後の[実行36025938367](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36025938367)（run `E2E-20260924T161539Z-3c9a4ef5`）では、実Google通知3回のAlarm実行・通常同期・往復・成功後の重複抑止と、同期中／Google取得失敗後の同番号再試行が成功した。artifactは `passed`、全manifest `dirty=false`、watch状態なし、共有KV・通知キュー・Alarm回収成功。監査108行・54操作、run/version/commitを独立照合した。

このworkflow自体は最終同期後の `read_status` の `worker_request_failed` で失敗した。API反映や回収失敗と混同しない。`fb28b94` で、状態読取りの通信失敗だけを最大3回再試行するよう修正した。401、run不一致、証跡不一致は再試行せず、prepare/trigger等の外部書込みも再実行しない。通信失敗の再現テストは修正前に失敗し、修正後はNode全293件成功した。

workflow全体の[再確認36027225893](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36027225893)は成功した。run `E2E-20260924T162654Z-e06be204`、commit `fb28b94c938f1e0a7baaf2216394059b6d9d38f1`。artifact `e2e-evidence-36027225893-1` と `pytest-junit-36027225893-1` を独立照合し、次を確認した。

- 実Google通知3回のAlarm、通常共有KV・global DOを使った同期、往復、成功後の重複抑止。
- 同期中と固定Google取得失敗後の同番号再試行は、ともに `retry_recovered=200`。
- `passed`、全サービス・シナリオmanifest `dirty=false`、watch状態なし。
- 通知キュー・Alarmの回収200、共有KV回収200、外部所有資源の回収200。
- 監査104行・52操作。run・稼働version・commit・clean checkoutの一致。
- JUnit816件成功、失敗・error・skipなし。

修正は `feature/watch-shared-e2e` へpushした。専用E2E Workerへの反映と検証であり、本番Workerへのデプロイ、実Cron、Google自身による同番号の自動再配信、自然なAPI障害の検証は含まない。

## 現行commitでの再確認

2026-09-25の[実行36117345631](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36117345631)は、commit `b2534037167b11c394aaed9070c780e575d47825`、run `E2E-20260925T091613Z-299612c8` で失敗した。

- watch登録、有効時の無更新、期限しきい値・期限欠損による更新、token変更と復元、停止後の再登録はすべて200。
- `prepare_webhook` 後の制御ロック解放で `google_sync_release_failed`。診断は `step=release_rpc`、`exception=js_exception`、応答判定は未取得。例外の詳細原因は未確定。
- manifestは `stage=ready`、`dirty=true`。step 0の別HTTP検証と実変更通知3回には未到達であり、共有Webhook同期の成功とは扱わない。
- 通常とalwaysの回収計8回は `google_sync_busy`・503。watchと所有資源・共有状態の回収はこの実行では未完了。
- artifact `e2e-evidence-36117345631-1` と `pytest-junit-36117345631-1` を独立照合した。監査20行・10操作、run・稼働version・commit・clean checkoutが一致。JUnit967件成功、失敗・error・skipなし。

同runの[回収専用実行36117671982](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36117671982)は成功した。制御ロックの300秒TTL経過後、同commitを別versionへ再deployし、所有資源だけを回収した。artifact `e2e-evidence-36117671982-1` を独立照合し、次を確認した。

- 同run・commit、旧versionから新versionへの更新と稼働versionの一致、実行checkoutのclean状態。
- `failed_clean`、全サービス・シナリオmanifest `dirty=false`、watch状態なし。
- `watch_shared_cleanup=200`、`watch_shared_queue_cleanup=200`、`google_sync_shared_cleanup=200`、所有Google予定の削除204、回収全体200。
- 監査4行・2操作、失敗操作なし。回収workflowのJUnitも967件成功。

実Webhook共有同期は今回未検証であり、以前の成功記録で今回の失敗を置き換えない。今回、実装変更・本番デプロイは行っていない。ロック解放失敗の詳細原因の調査と、解消後の再実行を残作業とする。

## ロック解放失敗の原因調査

2026-09-25に失敗runの実ログと実装を調査した。元の失敗は `_release_control` → `StateStore._sync_do_rpc` → DOの `sync_state` の解放呼出しで `JsException` となった。解放応答を取得できず、後続の回収は制御ロックの300秒TTL経過まで拒否された。例外型だけでは、接続切断、DO再起動、実行制限、Python/JavaScript境界の不具合を区別できない。

失敗時刻09:16:35–09:17:45 UTCの242件、回収時刻09:22:00–09:23:30 UTCの177件を読取り診断した。失敗時間帯のHTTP本体はwall time 42.972秒・CPU 1.010秒・outcome `ok`。例外を捕捉して409を返しているため、runtimeの `ok` はシナリオ成功を意味しない。runtime警告5件があるが、[診断36120479205](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36120479205)までの固定分類では原因を確定できなかった。取得件数の不足はなく、生ログ・例外本文・秘密値は保存していない。

`79ec7f2` でE2E解放失敗の固定原因分類と、別stubでのロック状態読取りを追加した。接続の故障とロック残留を区別するための診断であり、解放の再試行や接続変更による修正は含まない。MCP応答・監査・manifestも許可した分類と真偽値だけを保持する。既存の未知例外の秘匿、新しい診断読取り自体の失敗もテストした。

診断版の[再実行36119459889](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36119459889)は成功した。

- run `E2E-20260925T093843Z-82a820c4`、commit `79ec7f25f5d3f43845a5666c3cfc604d67c52ee2`。run・稼働version・commit・clean checkoutを独立照合。
- watch維持、全4段階、実Google通知3回のAlarm・通常共有同期、重複抑止、同期中／固定API拒否後の再試行が成功。
- `passed`、全manifest `dirty=false`、watch状態なし、全所有資源・共有KV・通知キュー／Alarmの回収成功。
- 監査106行・53操作。verifyの想定待機は `google_sync_not_ready` 17回、`google_sync_busy` 25回。ロック解放失敗は再発しなかった。
- JUnit975件、ローカルNode318件、Ruff、Pyright、E2E secret hygiene・workflow検査が成功。最初のPyrightは仮想環境未有効化によるimport解決エラーとなり、`.venv` 有効化後に0 errorsを確認した。

取得時のstubを長い処理後に再利用している点と、watch停止応答の本文を消費しない点は調査候補である。しかし再実行成功や静的な疑いだけでは、元の失敗原因と断定できない。[CloudflareのDOエラー処理](https://developers.cloudflare.com/durable-objects/best-practices/error-handling/)は、例外後のstub再作成を推奨するが、今回の元の例外原因を証明する資料ではない。診断は専用E2E Workerへ反映済み。本番Workerは変更していない。

[追加診断36120734870](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36120734870)では全419件にメッセージが含まれることを確認した。警告5件の固定語は `response`・`body` のみ。接続切断・RPC・タイムアウト・実行制限・停止応答未消費によるdeadlockの固定分類には一致しなかった。メッセージのフィールド欠落による見落としとは区別できたが、元の解放例外の原因は特定できていない。

2回目の診断付き[実行36120901864](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36120901864)も成功した。run `E2E-20260925T095454Z-25fef03a`、commit `611a74a7356478aca15df15fa0b04df775211f85`。全4段階・実通知3回・再試行・回収、`passed`・全manifest `dirty=false`、watch状態なし、run/version/commit・clean checkout一致を独立照合した。監査128行・64操作、JUnit975件成功。verifyの想定待機はnot_ready 26回・busy 27回で、ロック解放失敗はない。

結論は「失敗箇所とロック残留は確定、根本原因は未特定」。元の例外本文は保存されておらず、診断付きの再実行2回でも再発しなかった。追加診断は次回失敗時の原因分類と別接続の読取りを可能にするが、原因の特定や修正完了を意味しない。2回の検証で作成した資源は回収済み。
