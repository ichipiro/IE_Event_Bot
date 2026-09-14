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

- [ ] KVの古いsnapshot / queue参照、外部成功後の保存失敗を検証する。
- [ ] ロックTTL超過と期限切れ後の別実行を検証する。
- [ ] 結果に応じて追加修正し、再実行と整合性の保証範囲を確定する。

## 4. 通常Google同期

- [ ] 共有cursor・対応表・queue、全Calendar取得、Notion・Discord反映を検証する。
- [ ] 複数件、更新・削除、部分失敗、再試行、cursor更新を検証する。

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
