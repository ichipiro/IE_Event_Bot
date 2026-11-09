# 全体同期の取得後クールダウン修正・検証記録

## 対象と状態

2026-11-09 13:36 JSTのイベント作成通知の重複調査で、正常終了する同期2回が同じ通知を投稿できる条件を再現した。今回の修正は、そのうち開始判定とロック取得の間の競合を対象とする。本番の発生順の確定とは区別する。

実装commitは `d310ca8d0feb0eb1421ab376abc6c9fd69e0976c`。先の承認済み同期修正・E2E整理とともに[正規フロー](../fork-upstream-workflow.md)で統合し、v0.7.0として本番反映・稼働版照合まで完了した。元の本番事象の発生順は未確定である。

## 修正とローカル検証

global DOのロック取得後、ownerとDOの最終成功時刻を確認し、クールダウン中なら同期本体へ進まない。manual・Cronは200、Webhookは503の既存の`cooldown_skip`を維持し、ロックはfinallyで解放する。

[回帰テスト](../../tests/test_sync_guarantee_boundaries.py)の `test_post_lock_cooldown_prevents_duplicate_notification` は、旧実装で2件失敗・1件成功、修正後は3件すべて成功した。取得前の競合と古いsnapshot、競合と最新snapshot、300秒以内の直列実行で、同一イベントのPOST・リアクションは1回、後続のsnapshot読取りは0回、ロック残留は0件となる。

- Python: 1,243件成功。外部通信は代替実装で遮断。
- Node: デプロイ診断追加後のMCP・workflow契約440件、Cron契約13件成功。Pythonの全体検査は1,243件のままで、診断追加によるWorker本体の変更はない。
- Ruff・Pyright、E2E設定・機密保護・workflow検査成功。
- 通常Worker・専用E2E WorkerのWrangler dry-run成功。実デプロイではない。

## 実サービス検証

専用E2E Workerで次の全4モードが成功した。完了時刻は2026-11-10のJSTである。

| モード | 実行 | source SHA | 完了時刻 | 監査行／操作 | 確認の再試行 |
| --- | --- | --- | --- | --- | --- |
| all-sync | [42647189757](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/42647189757) | `696d0f6` | 00:50:44.524 | 42／21 | 0 |
| all-http | [42653543691](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/42653543691) | `dcda063` | 01:25:50.521 | 22／11 | 0 |
| watch-shared | [42654734843](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/42654734843) | `dcda063` | 01:34:32.028 | 82／41 | 30 |
| 通知専用 | [42656512602](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/42656512602) | `dcda063` | 01:40:48.848 | 18／9 | 0 |

- all-sync: run `E2E-20261109T154336Z-c184f8c5`。全9段階、認証欠落時の200、実Notion検索拒否400、残件復旧、クールダウン・競合の制御を確認した。
- all-http: run `E2E-20261109T162135Z-141be8be`。全4段階、通常HTTP dispatch 3回と共有状態の回収を確認した。
- watch-shared: run `E2E-20261109T162816Z-eb21de9a`。全4段階、通常HTTP・実callback・Alarm各3回、旧token拒否401、旧channelの404／204／204、制御ロックの復旧、watch・通知キュー・Alarmの回収を確認した。30回は確認用resumeだけの待機再試行で、409 `google_sync_not_ready` が7回、503 `google_sync_busy` が23回。後続resumeの200で解消し、未解決のまま成功とはしていない。
- 通知専用: run `E2E-20261109T163840Z-f32c7698`、`discord_batch_notification`の28検証stageが成功した。所有2件の通常ポーリング、上限1件による繰越、同じmessageへのリアクション再試行、通知2件の最終読戻しを確認した。通知削除204と削除後404、Discord予定削除204、Notion archive 200を各2件、KV回収と通常／常時cleanupの成功を照合した。確認の再試行は0回だった。リアクションの最初の失敗はAPI呼出し前の固定注入で、実Discord障害の観測ではない。

各実行のcheckout SHA・run・version tag、deployと最終versionのfingerprint、監査の開始／終了とmanifest操作数を照合した。今回runの`passed`、全service／scenario manifestの`dirty=false`、所有資源の回収を確認した。Google同期の3モードではactive一覧が空であることも確認した。別runの過去の`passed`は今回の成功へ数えない。Google同期3モードの集約照合は`test-results/notification-cooldown-20261110/verification.json`、各artifactと個別照合は`test-results/notification-cooldown-<実行番号>/`、通知専用の証跡と個別照合は`test-results/cooldown-notification-42656512602/`へ保存した。

all-syncのsourceは`696d0f67f4a8e75a7b951f0cb683257686864195`、後続3モードは`dcda0631a81ff7fdf5f488c74d0a679d14f24c3e`である。差分はMCPデプロイ診断とそのNodeテストの2ファイルだけで、`workers/`全体、Python・Nodeの依存定義／lock、`typings/`のGit object一致を確認した。リポジトリ内のruntimeビルド入力の同一性を根拠に最初の成功を保持し、実行ごとのsource SHAは区別する。デプロイ済みバイナリや外部状態まで同一とする主張ではない。

### all-httpの先行2失敗

次の2回はscenario開始前に停止し、成功には数えない。

| 実行 | Wrangler終了status | 失敗理由 |
| --- | --- | --- |
| [42648795897](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/42648795897) | 1 | `wrangler_deploy_failed` |
| [42649745400](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/42649745400) | 0 | `worker_version_propagation_timeout` |

両方ともsourceは`696d0f6`で、監査はdeployの開始／終了2行・1操作だけだった。scenarioのprepare・fixture作成はなく、今回runの所有資源は作成されていない。証跡が示すWorker tagとcleanなGoogle同期manifestは、前のall-sync run `E2E-20261109T154336Z-c184f8c5`のものである。全manifestの`dirty=false`を確認したが、古い`passed`を失敗runの成功と解釈しない。

1回目の詳細なCLI原因と、2回目に公開応答の版確認が完了しなかった原因は未特定である。2回目の終了status 0から、デプロイ自体が行われなかったとは断定しない。診断追加後の再実行成功も、先の根本原因を特定・修正した証明にはしない。

## 本番反映

- ユーザーの最終承認後、[v0.7.0](https://github.com/ichipiro/IE_Event_Bot/releases/tag/v0.7.0)・SHA `c9eb0709fda2fcf176589ff90b6a8e315894f648` を本番 `ie-event-bot` へ反映した。2026-11-10 03:02:34 JSTに100%へ切り替え、03:07:43 JSTに稼働版のtag・Version ID・SHAを再照合した。
- 変数33件・Secret 5件を引き継ぐ設定で候補をアップロードし、bindingの名前・型、既知変数のdigest、既存KV/DO namespace・互換設定・migration tagの一致を確認した。認証値を取得せず、Cron・ログ設定を再適用せず、DOマイグレーションも送信していない。
- 反映後の `/health` は200・`ok=true`。新版のVersion IDが一致するHTTP 3件・定期実行1件・その他12件を73.52秒観測し、全16実行が`outcome=ok`、例外・エラーログ0件だった。定期実行のCPUは155ms、Wallは17,698msで、切り戻しは不要だった。
- 最初のtailコマンドはsampling-rate引数で拒否された。引数を省いて再監視し、失敗記録をWorkerの実行結果とは別に保持した。最終照合は `test-results/production-deployment-20261110-0242/final-verification.json`、候補・反映・監視の証跡も同ディレクトリに保存した。ログ本文やHTTPヘッダーは保存していない。
- npm脆弱性13件（critical 1・high 8・moderate 4）は承認時に提示した未解決事項であり、依存更新はしていない。CPU上限と配置設定値の個別取得も未実施。今回の観測は短時間の稼働確認で、各外部APIの業務結果を個別照合したものではない。

## 保証の境界

今回の競合の直接証拠は、通常Application・StateStore・同じglobal DOを通したローカル回帰である。実行順と古い読取りは固定モデルであり、実KVの遅延や本番での発生頻度を測っていない。

全体同期E2Eは作成通知を無効化し、通知専用E2Eは追加のDO所有記録で資源を保護する。通知専用のartifactは2通知の読戻しを示すが、Discord POST総数を直接計測しておらず、取得後クールダウンの競合も実行しない。その競合修正の直接検証は前述のローカル回帰である。そのため、実E2Eの成功も任意の通常通知の一度だけの配信保証ではない。単独Discord同期、300秒経過後の古いsnapshot、投稿応答喪失・投稿後の保存失敗、別namespace、ロック期限超過の限界は[保証範囲](../TESTING.md#状態障害の保証範囲表)に残す。
