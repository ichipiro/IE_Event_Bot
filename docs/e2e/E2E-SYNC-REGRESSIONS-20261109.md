# 同期・CIの回帰修正（2026-11-09）

状態: ローカル回帰確認済み、実E2Eは実行準備中。

## 修正対象6件

| 項目 | 問題 | 修正と回帰対象 |
| --- | --- | --- |
| 1 | Discord→Google同期が有効でもGoogle認証を解決できないと、Googleへの反映を省略したまま成功扱いになる | 作成・更新・削除を失敗としてqueueへ残し、認証回復後に再試行する |
| 2 | Notion検索の失敗を0件と扱い、重複作成や削除の取りこぼしが起きる | HTTP失敗・不正応答を0件と区別し、変更をqueueへ残す。検索例外も差分処理で失敗として扱う |
| 3 | 往復同期で既存Google予定の由来がDiscordへ変わり、その後のGoogle編集が届かない | 既存予定の由来情報を保持する。内容を照合して重複反映を抑止し、Googleの実編集は反映する |
| 4 | PRブランチ名がworkflowのシェル本文へ直接展開される | `HEAD`・`BASE`をstepの`env`から受け取り、branchの文字列を命令として実行しない |
| 5 | Calendar設定を変えても、有効期限内の旧watchが維持される | 保存済みCalendarと現設定が違う、または保存値が欠落する場合にwatchを更新する |
| 6 | DOの期限切れWebhook重複抑止レコードが残り続ける | 通知操作時に期限切れの通常レコードを有限件ずつ巡回して掃除する |

項目3では、既存の誤ったorigin属性を自動移行しない。今回の修正後に由来を誤って上書きしないことと、過去データの修復は別の作業である。

内容の指紋はGoogleの実編集をDiscordへ反映した後にも更新する。本文のA→B→Aという復元編集を古い指紋で見逃さず、指紋更新の失敗後はDiscordへ再適用せず記録だけを再試行する。記録待ち中の新しいGoogle編集・取消は優先する。場所を空にする変更もGoogleへ反映する。

## 旧コードの再現とローカル回帰

旧コードで失敗する再現テストを追加し、修正後も同じ入力・期待結果で確認する。外部API、KV、DOは代替実装を使う。

| 対象 | 回帰テスト | 主な確認内容 |
| --- | --- | --- |
| 1・2・3 | [Discord同期障害](../../tests/test_discord_sync_failures.py) | 認証欠落、検索のHTTP失敗・不正応答、queueからの復旧、由来情報の保持 |
| 3の往復時の副作用 | [内容の再取得](../../tests/test_google_content_echo.py) | 同一内容の再取得でDiscordの新しい編集を消さず、Googleの実編集は反映する |
| 4 | [PR Target Guard](../../tests/test_pr_target_guard.py) | Gitで有効な命令置換・引用符入りbranch、未対応prefix、正しいbaseと誤ったbase |
| 5 | [Google watch](../../tests/test_google_watch.py) | Calendar変更・保存値欠落の更新と、同じCalendarの維持 |
| 6 | [dedupe掃除](../../tests/test_google_dedupe_cleanup.py) | 有効claim・E2E所有・他prefix・別DO・Alarmの保護、巡回、再起動、削除前再読込、JS境界 |
| E2Eの判定 | [all-sync](../../tests/test_e2e_all_sync.py)、[Node workflow](../../tools/run_e2e_workflow.test.mjs) | 障害証跡や残件数が欠けた実行を成功にしない |

PR guardは修正前に命令実行を再現する4ケースが失敗し、修正後は通常ルールを含む15ケースが成功した。これはworkflowの式展開とシェル処理をローカルで再現した結果であり、GitHub上の実行結果ではない。最終コード全体の検査結果は実行結果欄へ追記する。

## all-syncの強化

`deploy-and-all-sync-smoke` は所有予定2件とrun別の状態を使い、全9段階と各段階の別HTTP読戻しを行う。Google・Discordの両方向、部分失敗後のqueue復旧、由来・本文・対応ID、クールダウンと競合を検証する。manual／webhook／cronはdispatchのsourceを切り替えるもので、実Cron配信の試験ではない。

step 6ではDiscord予定2件を変更し、通常のupsertへ次の条件を渡す。

1. 1件目はGoogle tokenを`None`へ固定注入する。Google反映を省略して成功にせず、未処理として残す。
2. 2件目は存在しないNotionプロパティで実APIの400を確認し、同じ設定を通常の検索へ渡す。この検索では`expected_internal_page_id`による代替の停止判定を使わない。
3. 誤って新規作成へ進む実装は、作成直前の所有資源ガードで外部APIへ送る前に停止し、試験失敗にする。
4. 通常dispatchの失敗、最終成功時刻の維持、queueとsnapshotの残件2件を別HTTPで確認する。次の段階で正常設定へ戻し、同じ対応IDへの復旧と残件0件を確認する。

workflowは`all_sync_failure_6=200`に加え、`all_sync_token_missing=200`と`all_sync_query_rejection=400`を必須証跡にする。400は意図した入力拒否であり、自然発生したNotion障害ではない。tokenの固定注入も認証サービスの自然障害を証明しない。

Google変更がDiscordへ届く修正に合わせて、step 4・5の期待本文も更新する。旧実装の誤った本文を保持できたことを試験成功にしない。

## 実E2Eの範囲と成功条件

| mode | 主な範囲 | 状態 |
| --- | --- | --- |
| `deploy-and-all-sync-smoke` | 上記9段階、両方向の障害とqueue復旧、source別のクールダウン・競合 | 実行準備中 |
| `deploy-and-all-http-smoke` | 全4段階。入力全件の所有確認後、runnerを差し替えず通常`Application.fetch`の`/sync/all`と共有KVを使う | 実行準備中 |
| `deploy-and-watch-shared-smoke` | 通常watch維持、実Google通知からDO Alarm・共有状態同期、通知の再試行、ロック解放復旧と回収 | 実行準備中 |

今回runのcommit・clean checkout、run ID、deploy時と最終読戻しのWorker version、各段階の必須証跡を照合する。途中の失敗はcleanup成功で上書きしない。対象manifestの`outcome=passed`と`dirty=false`、全所有資源・状態の回収、監査の開始・終了を確認する。watch試験では所有通知キューとAlarmも回収する。過去runの`passed`は今回の成功へ数えない。

これらは専用Worker・専用資源の有限ケースであり、本番稼働、任意件数、自然発生障害、厳密に一度だけの反映、全地域KV整合性は保証しない。dedupe削除条件の詳細はローカル回帰で確認し、watch実E2Eの成功だけで期限切れ通常レコードの全条件を実環境検証済みとは扱わない。

## E2E準備時のCalendar取得範囲

all-sync・all-http・watch-sharedの新規runは、prepare開始時のUTC実時刻を`initial_cursor`へ一度だけ記録する。最初に`updatedMin`を付けず`showDeleted=false`でactive予定を全ページ確認し、古いactive予定も拒否する。HTTP失敗・不正応答・ページ循環は空扱いにしない。GuildとNotion DBの空確認も維持する。

削除済み履歴のbaseline確認とall-httpの事前所有確認には、この固定cursorを通常差分取得へ渡す。通常処理の2分の重複範囲も含めて確認し、取得した未知の入力は拒否する。初期cursorは所有manifestで変更を禁止し、実処理の初期値も通常GoogleKVの書込み・hash・回収記録へ含める。保存直後の応答喪失でも所有runのcleanup対象にし、旧manifestのcursor欠落は従来の回収経路で扱う。

fixtureの将来の開催日時はcursorに使わない。cursorはprepare時の実時刻、予定の変更時刻はGoogle APIの`updated`であり、準備中の新しい変更も同じ窓へ含める。過去の削除履歴を消したり100件上限を広げたりせず、初期窓外の古い削除履歴は今回の差分入力に含めない。直近履歴が100件を超えれば引き続き拒否する。通常コードと、full・matrix・boundaryなど他モードの初期full取得は変更しない。

[初期cursorの回帰](../../tests/test_e2e_initial_cursor.py)では、古い削除履歴150件で旧実装の3モードが`google_sync_baseline_limit`となることを再現した。修正後は同じ履歴を保持した準備・dispatch・回収、古いactiveの拒否、100件上限、不変cursor、保存応答喪失、旧manifest回収とページ異常を確認する。

## 状態掃除の境界

`gcal_msg:`の通常レコードだけを対象に、1操作あたり最大100件を読み、`gcal_gc:cursor`で次の範囲へ進む。50msは一覧取得後のループ内予算であり、storage I/Oを含む処理全体の厳密な時間上限ではない。

有効期限内の処理中claim、E2E所有run付きの値、判定不能な形式は保持し、期限切れでも削除前に最新値を読み直す。既存WebhookキューのAlarmは変更しない。通知操作が止まると期限切れレコードは次の操作まで残り、無通信中に一定時間以内の回収を保証しない。

## 実行結果

- ローカル最終回帰: Ruff・Pyright成功、Python 1,212件、Node 383件・Cron契約13件、E2E設定・機密情報混入・workflow検査、通常WorkerとE2E Workerのdry-runが成功。
- 初回実E2E: [all-sync実行42620421858](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/42620421858)は、commit `18f2d76` のdeploy後、既存Calendarの削除履歴が100件を超え、`prepare_all`で`google_sync_baseline_limit`となった。今回runのowner保存・fixture作成前の停止であり、成功に数えない。全manifestはcleanで、artifact内の過去runの`passed`も今回の実績へ数えない。cleanupは`google_sync_run_mismatch`で旧runを保護した。マスク済み証跡は`test-results/sync-fixes-42620421858/verification.json`へ保存した。
- 修正後の実E2E: 実行準備中。完了後に各run・commit・監査・回収結果を追記する。

関連: [検証方法](../TESTING.md)、[バックエンド](../BACKEND.md)、[状態設計](../DB-SCHEMA.md)、[従来のE2E計画](E2E-PLAN.md)。
