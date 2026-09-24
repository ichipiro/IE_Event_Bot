# E2E残作業

2026-09-11のユーザー指定に基づく範囲。証拠は [WORKLOG.md](WORKLOG.md)、検証方法は [TESTING.md](TESTING.md) に記録する。

## 1. 通常同期の隔離と所有権

- [x] 通常StateStoreをrun・scope別のKVへ接続し、DO所有権・固定キー・値の検証を行う。
- [x] 保存・別HTTP読戻し・回収とMCP経路を実装し、拒否・部分失敗・回収再試行をローカル検証する。
- [x] 通常KV専用の手動workflowに読戻し待機・revision照合・失敗時の回収を接続する。
- [x] 実KV・DOで上記経路を検証する（[実行34604249166](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34604249166)）。
- [x] 外部fixture 1組の所有権を通常KVへ接続し、別HTTP検証と一括回収をローカル検証する。
- [x] 外部fixtureと通常KVの接続を実サービスで検証する（[実行34605517604](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34605517604)）。
- [x] 固定2件の所有・上限1件での適用・KV残件の別HTTP消化・回収を実装し、ローカル検証する。
- [x] 固定2件の適用・残件・回収を実サービスで検証する（[実行34614558706](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34614558706)）。

現在の `discord_state` はKVだけの補助シナリオであり、外部イベントを作成・同期しない。snapshot / queueは通常StateStoreで別々に保存し、DOには所有メタデータだけを置く。

## 2. 通常Discord同期

- [x] 通常ポーリングの一覧取得から所有2件のNotion反映・KV残件処理へ接続し、ローカル検証する。
- [x] 上記の通常ポーリング経由を実サービスで検証する（[実行34615847619](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34615847619)）。
- [x] 所有2件の通常ポーリングからGoogle・Notion反映へ接続し、固定ID・対応ID・部分失敗回収をローカル検証する。
- [x] 上記のGoogle反映を専用環境で実サービス検証する（[実行34619150601](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34619150601)）。
- [x] 固定2件のsnapshot / queue、件数上限、残件を実サービスで検証する（[実行34614558706](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34614558706)）。
- [x] 通常ポーリングの通知繰越・同期失敗後の通知漏れを修正し、投稿・リアクションの再試行をローカル検証する。
- [x] 作成通知・再試行を所有メッセージの記録・回収、通常ポーリング、専用workflowへ接続し、ローカル検証する。
- [x] `discord_batch_notification` を実サービスで実行し、通知・再試行・全所有資源の回収と証跡を確認する（[実行34831533775](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34831533775)）。
- [x] 手動・単独Cron・全体同期の共通処理を使うロック競合E2E、結果KVの所有・回収、専用workflowを実装し、ローカル検証する。
- [x] `sync_lock` で共通処理の競合・結果保護・解放を実KV・DOで検証する（[実行34834547224](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34834547224)）。HTTP内の並行呼出しであり、実Cron配信は項目8で扱う。

## 3. 状態とロックの障害検証

- [x] `sync_faults` に固定の古い値3ケース・保存前後失敗4ケース・TTL超過1ケースを実装し、MCP・手動workflow・所有KV読戻し・回収へ接続してローカル検証する。
- [x] 期限・所有者を失った旧実行の結果保存を拒否し、手動・Cron分岐・全体同期とGoogle適用後の境界をローカル検証する。

- [x] 専用環境の `sync_faults` を実行し、固定注入モデルと実KV書込み・別HTTP読戻しを照合する。[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)で成功。外部成功は代替runnerであり、実サービス障害・実KV伝播遅延の観測とは分ける。
- [x] 実環境でのphase時間超過に備え、状態障害8ケースを1 HTTPずつに分割し、途中失敗時の再実行拒否と回収をローカル検証する。
- [x] ロックTTL超過と期限切れ後の別実行を検証する。[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)で手動同期共通処理の旧実行拒否・新owner保護と完了を確認。実Cron・別Workerリクエスト間の競合は対象外。
- [x] snapshotにも未処理操作を保持し、古いqueueによる作成・更新・削除・件数上限残件・通知待ちの喪失を防ぐ修正をローカル検証する。
- [x] 専用環境で残件回復を再検証する。[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)の固定 `stale_queue_loss` ケースと状態読戻しが成功。
- [x] 両キーとも古い場合・旧形式データ・重複適用・通知の一度限り配信・期限確認と書込み間の競合を含め、保証範囲を確定する。[保証範囲表](TESTING.md#状態障害の保証範囲表)と追加8ケースで、保護できる条件と残件喪失・重複・結果上書きが起きる条件を区別した。制限の解消や実サービス障害の再現完了は意味しない。

## 4. 通常Google同期

- [x] 通常dispatch → Google全ページ取得 → 通常適用 → 状態保存を接続したローカル検証を追加する。複数件・上限繰越・対応表・更新・削除・照会例外の再試行・取得失敗時のcursor保護を代替APIで確認した。
- [x] `google_sync` に所有2件、run別KV・DO所有記録、繰越・消化・更新・削除の段階処理、別HTTP読戻し、回収、MCP・手動workflowを実装し、ローカル検証する。
- [x] run別KVのcursor・対応表・queueを別HTTP間で共有し、全Calendar取得から所有2件のNotion・Discord反映、上限繰越・消化・更新・削除・cursor更新・回収を実サービスで検証する（[実行34862331643](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34862331643)）。
- [x] Discord作成・更新失敗の成功誤判定を再現・修正し、部分反映・残件保存・cursor保護・次回のqueue処理をローカル検証する。
- [x] 所有予定へのDiscord失敗の固定注入と、次のHTTPでのqueueだけの再試行を既存E2E・MCP・手動workflowへ接続する。
- [x] 上記の部分失敗・再試行モデルを実API反映と実KV・DOで検証する（[実行34866761198](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34866761198)）。固定注入後の部分反映・cursor保護・次のHTTPでのqueue回復と全資源回収が成功。実際のDiscord障害の観測ではない。
- [x] Notion照会・取得・作成・archive・Discord ID書戻し、Discord削除のHTTP失敗とsubrequest上限での残件消失・成功誤判定を再現・修正し、queueだけでの回復をローカル検証する。
- [x] 所有Discord予定への不正な更新によるHTTP 400・code 50035の確認を既存E2Eへ追加し、次のHTTPでの回復、想定外応答の拒否、workflow証跡の必須化をローカル検証する。
- [ ] 上記のAPI拒否・queue再試行を既存E2E専用環境で実行し、全所有資源の回収とartifactを照合する。2026-09-24にユーザーが同環境の使用と専用であることを確認した。入力検証による拒否とサービス障害は区別する。
- [ ] 初回[実行35952552380](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35952552380)のdirty資源を回収する。API拒否段階と所有確認に失敗し、回収専用workflowと空名の場合の限定的な所有照合をローカル検証した。回収確認後に不正日時を使う修正版E2Eを再実行する。
- [ ] 通常の共有名前空間と任意イベント全件への適用、外部APIの実障害・途中再試行を実サービスで検証する。

実サービス用の接続実装と所有2件の実環境検証が完了した。[初回実行34861663237](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34861663237)はprepareで失敗して全資源を回収した。KV欠損値の正規化漏れをローカルで再現・修正した後の再実行は、全段階・回収が成功し `passed`・全資源 `dirty=false` となった。全Calendarを取得しても適用対象は所有2件へ限定する。任意イベントの全件適用、外部APIの全失敗分岐・途中再試行は引き続き未検証である。

## 5. 全体同期

- [ ] Google・Discordの両同期、反映の往復、部分失敗を検証する。
- [ ] クールダウン、最終時刻・結果、手動・Webhook・Cron間の排他を検証する。

## 6. watch維持とWebhook

- [ ] 通常watchの登録・更新・再登録、token変更、旧通知を検証する。
- [ ] 実変更通知から共有状態を使う同期へ接続し、重複・実行中通知・再試行を検証する。

## 7. 通常ジョブ

- [ ] Q&Aの全件取得・質問番号補完・共有cache・通知を検証する。
- [ ] リマインドの全件取得・対象選別・共有cache・通知を検証する。
- [ ] Notion cleanupの全件取得・期限判定・共有最終時刻・実行間隔を検証する。
- [ ] 各ジョブの再試行、重複抑止、所有資源回収を検証する。

## 8. 実Cron

- [ ] 隔離環境で期間と対象を限定し、Cloudflareの実Cron起動を確認する。
- [ ] 手動実行との競合を検証し、終了後にスケジュールと所有資源を回収する。

## 9. 実行・復旧・証跡

- [ ] 追加シナリオをMCP・手動workflowへ接続し、対象revisionを照合する。
- [ ] 各段階のローカル検査・dry-run・専用環境検証を実施する。
- [ ] 成功・部分失敗・再試行後のcleanupまたはdirty記録を確認し、マスク済みartifactを独立照合する。

## 10. 追加対象外

ユーザー指定により、次の5件は追加実装・追加試験・完了条件に含めない。既存の証拠と未検証の境界は保持する。

- 実際の回線断、処理完了前の中断、Worker途中停止。
- DOプロセスの強制再起動後の復元。
- 入口ロックを越えたDO claimそのものの実環境競合。
- キャンセル後もDiscord一覧に残る分岐の実サービス観測。
- 任意の外部書き込み位置からの自動再開。

項目3のKV保存失敗・古い値の参照・ロックTTL超過の検証は継続対象とする。

## 11. 文書・課題管理

- [ ] 実装と証拠に合わせて標準文書・Issue #17を更新する。
- [ ] 対象内の完了条件を満たした後、Issueを完了にする。

## 12. リリース・本番反映

- [ ] 追加変更のPR・CI・マージ・fork同期を行う。
- [ ] 正式リリース対象を確定し、upstreamのリリース工程を行う。
- [ ] 本番binding・Secret登録状態・変数・Cron設定を確認し、デプロイと稼働versionの照合を行う。
- [ ] 通常同期・Webhook・Cronの稼働結果と復旧手順を確認する。

実装順は1→2・3→4→5→6・7→8とする。9と11を各段階で実施し、最後に12へ進む。Gitマージ、Release、デプロイは別工程として記録する。
