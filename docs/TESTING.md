# ローカルテスト

## 目的

この文書は、IE Event Bot の単体テストを Linux / WSL の CPython 上で再現する手順と、テストで保証できる境界を定義する。

テストは Cloudflare、Discord、Google、Notion の実環境へ接続しない。認証情報も使用しない。

## セットアップ

リポジトリルートの仮想環境へ開発依存を導入する。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install -r workers/requirements.txt
```

既存の `.venv` が利用できる場合は、作り直す必要はない。

## 実行方法

全テスト:

```bash
.venv/bin/python -m pytest -q
```

ファイル単位:

```bash
.venv/bin/python -m pytest -q tests/test_entry.py
```

テスト単位:

```bash
.venv/bin/python -m pytest -q \
  tests/test_entry.py::test_webhook_duplicate_dispatches_only_once
```

CI は Ruff、Pyright の後に全テストを実行する。テストが収集できない場合も成功扱いにはしない。

E2E オーケストレーター MCP のローカル契約テストと設定検査:

```bash
npm run test:mcp
.venv/bin/python tools/validate_e2e_mcp_config.py
.venv/bin/python tools/validate_e2e_secret_hygiene.py
.venv/bin/python tools/validate_e2e_workflow.py
bash -n tools/configure_github_e2e_environment.sh
```

これらも通信先をテスト代替へ差し替えるか、設定ファイルだけを読む。実 Worker や外部サービスへは接続しない。

## 通常Discord同期の状態保存と排他

`tests/test_discord_state_recovery.py` は通常の `StateStore` と差分処理を使い、KV・外部APIを代替する。作成・更新・削除の再試行、queue / snapshotの保存失敗、件数上限による残件を、新しいStateStoreで読み直して確認する。保存失敗後の成功済み操作の再実行は許容し、未処理操作が消えないことを確認する。

`tests/test_discord_notification_retry.py` は同じ通常ポーリングとStateStoreを使い、一覧取得・同期先・Discord APIを代替する。上限超過、同期失敗後の通知、投稿失敗、リアクション失敗、通知待ちのイベント変更・削除、完了イベントの保護、通知の件数上限、通知先変更・無効化、旧queue互換、不正な通知状態での書込み拒否を確認する。最初の4件の再現テストが修正前に失敗し、修正後は22件すべて成功した。

投稿成功後のmessage IDを保存し、リアクションだけの再試行では再投稿しない。Discordの[Create Reaction](https://docs.discord.com/developers/resources/message#create-reaction)は対象messageへのPUTで成功時204を返す。ローカルテストはこのAPI境界を代替する。実サービス配信と同じmessageへの再試行は、末尾の専用シナリオで確認した。投稿応答の喪失、KV保存失敗・古い値の参照を含めた一度だけの配信は保証しない。既存の `discord_batch` / `discord_batch_google` は通知先を隠す。通知の所有・回収と専用workflowへの接続は `discord_batch_notification` で検証する。

`tests/test_discord_sync_lock.py` は手動・Cronと全体同期の共通ロック、競合時の最終結果保護、適用・結果保存の例外とキャンセル後の解放、取得エラー時の停止、明示無効時の互換性を確認する。並行HTTPの検証では、1件が適用中の間にもう1件が409で拒否されることを確認する。

これらはローカル検証であり、実KVの伝播遅延、ロックTTL超過、実Cron、通常Guild全件への実サービス適用は未検証である。run所有checkpointを使う専用Discord差分E2Eの成功も、通常の共有KVの実動作を証明しない。


## 手動 E2E workflow

通常KVの隔離基盤は `tests/test_e2e_discord_kv_state.py` で検証する。専用Workerの `POST /admin/e2e/discord-state` がDOへ所有run・scope・対象fingerprintを保存してから、通常StateStoreで固定2キーへfixtureを書く。別HTTPの `/verify` で読み直し、`/cleanup` で所有2キーだけを削除する。外部サービスAPIは呼ばない。

MCPでは `trigger_sync(scenario="discord_state", sync_phase="prepare")`、同scenarioの `sync_phase="resume"`、`cleanup_run(service="discord_state")` を順に使う。保存・検証はrun IDとWorker version tagの一致を必須とし、回収は古いversionでも同run・対象の一致を確認する。全経路で認証・POST・globalロックを要求する。補助シナリオの無効状態は既存シナリオのpreflightを阻害しないが、dirty記録は共通statusと回収対象へ含める。

`E2E_STATE_SCOPE` は専用KVの論理識別子であり、Cloudflare namespaceの実IDを検証するものではない。bindingの実対象はデプロイ設定で別途確認する。KVの値をDOやアダプターへキャッシュせず、古い値・不正値を検証成功にしない。cleanupは固定キーのdelete完了を確認するもので、全拠点への削除伝播完了を保証しない。削除・manifest更新失敗ではdirtyを保持して再試行する。通常ポーリングと外部fixtureは未接続である。

通常KVの手動モードは専用Workerを1回deployし、保存1回、別HTTPの読戻し、所有2キーの回収を行う。同run・dirty=true・HTTP 409の `discord_state_not_ready` だけを3秒間隔で最大25回まで検証し、保存要求は再送しない。検証後のmanifestとdeploy時のversion fingerprintを照合し、回収後の `outcome=passed` を必須とする。検証失敗はcleanup成功で上書きしない。cleanupは最大4回、失敗時1秒間隔で試み、workflowのalways処理でも監査に記録された同runの資源だけを回収する。2026-09-11の[実行34604249166](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34604249166)では保存・別HTTP検証が各1回で成功し、回収後の `outcome=passed`、全資源の `dirty=false`、deployと最終Workerのversion一致をartifactで独立照合した。古いKV値による待機は発生していない。

外部fixture付きの `discord_kv` は、Discord event 1件とNotion page 1件を作成前から同じDO manifestで所有する。専用scopeを先に固定し、Discord ID確定後だけ通常StateStoreの固定2キーを公開する。通常の `_apply_discord_event_diff` に所有1件を渡し、作成通知・Google同期は無効とする。適用時のNotion検索は、既存page・検索失敗・不正応答で新規作成を止める。通常の呼出しでは従来の検索動作を維持する。

`POST /admin/e2e/discord-kv` で準備し、別HTTPの `/verify` で外部資源の所有権・内容とKVのsnapshot / queueを読み直す。`/cleanup` は外部資源を回収した後に所有KVを削除し、両方が完了してからcleanにする。KV削除失敗時もrun・scope・対象をDOに残す。DOにはsnapshot / queueを複製しない。各経路は認証・POST・globalロックを必須とし、準備・検証はWorker version tagとrunの一致を要求する。実行フラグは `E2E_DISCORD_KV_ENABLED` で、未設定なら無効である。

MCPは `trigger_sync(scenario="discord_kv", sync_phase="prepare" / "resume")` と `cleanup_run(service="discord_kv")` を使う。手動モードはfixture作成を1回に限定し、同run・dirty=true・HTTP 409の `discord_kv_not_ready` だけを3秒間隔・最大25回まで待つ。検証失敗は回収成功で上書きしない。ローカル代替APIでは部分保存・削除失敗・所有権不一致・古いsnapshot・再回収を確認した。2026-09-11の[実行34605517604](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34605517604)で準備・別HTTP検証が各1回で成功し、通常差分処理の適用・KV読戻し・Discord削除204・Notion archive 200・KV削除完了を確認した。監査とmanifestを独立取得し、version・runの一致、outcome=passed、全資源dirty=falseを照合した。読戻し待機は発生していない。cleanupは外部APIの削除・archive応答とKVのdelete完了を確認するもので、全拠点の削除反映を保証しない。この1件モードでは複数イベントと残件を扱わない。通常ポーリング、通知、TTL超過は未接続・未検証である。


追加対象外の5件は [E2E-PLAN.md](E2E-PLAN.md#10-追加対象外) に定義する。

`.github/workflows/e2e-staging.yml` は `workflow_dispatch` 専用であり、PR、push、schedule からは起動しない。forkではこのworkflowを登録するため、既定ブランチを`develop`とする。最初の job でローカル検査と Wrangler dry-runを行い、成功後に `e2e` GitHub Environment の承認を待つ。`GITHUB_TOKEN` は `contents: read` だけに限定する。deploy時はrun IDをWorker version tagへ指定し、専用Workerの`CF_VERSION_METADATA.tag`から同じ値を読み戻すまで書き込みscenarioを開始しない。これによりWrangler終了直後に旧revisionへrequestが届いた場合を成功扱いしない。

実行モード:

| モード | 外部操作 |
| --- | --- |
| `preflight` | E2E Worker の health とマスク済み status を読む。既定値であり、deploy と外部 CRUD は行わない |
| `deploy-and-crud-smoke` | 専用 Worker を deploy し、Google、Discord、Notion の自己 cleanup 型 CRUD probe と所有状態確認を順に行う |
| `deploy-and-discord-google-smoke` | 専用 Worker を deploy し、Discord Scheduled Event を既存の適用処理で Google event へ反映して検証後、両資源を cleanup する |
| `deploy-and-discord-notion-smoke` | 専用 Worker を deploy し、Discord Scheduled Event を既存の適用処理で Notion 内部 DB へ反映して検証後、両資源を cleanup する |
| `deploy-and-discord-delta-recovery` | 明示した `recovery_run_id` をversion tagにして修正版をdeployし、そのrunのDiscord差分資源だけをcleanupする。新規fixtureは作成しない |
| `deploy-and-discord-delta-smoke` | 専用 Worker を deploy し、Discord一覧のrun所有1件で新規作成・変更なし・更新・キャンセル・削除を共通差分処理へ通し、Notion pageの反映とarchiveを検証後に両資源をcleanupする |
| `deploy-and-discord-state-smoke` | 専用Workerの通常StateStoreで固定2キーを保存し、別HTTPで読戻しとversionを照合後、所有キーを回収する |
| `deploy-and-sync-lock-smoke` | 通常同期の共通ロック競合・成功/例外後の解放、所有結果KVの読戻し・回収を検証する |
| `deploy-and-sync-faults-smoke` | 固定KV障害モデル7ケースと10秒TTL超過、結果読戻し・回収を検証する。外部同期は代替runner |
| `deploy-and-discord-kv-smoke` | 所有Discord event 1件を通常差分処理でNotionとKVへ反映し、別HTTPで読み直して外部資源と固定2キーを回収する |
| `deploy-and-discord-batch-smoke` | 所有Discord event 2件を上限1件ずつNotionへ反映し、KVの残件を別HTTPで読み直して消化・回収する |
| `deploy-and-discord-batch-google-smoke` | 所有2件を通常ポーリングからGoogle・Notionへ反映し、対応ID・KV残件・全資源回収を確認する |
| `deploy-and-google-discord-smoke` | 専用 Worker を deploy し、Google event を既存の適用処理で Discord Scheduled Event へ反映して検証後、両資源を cleanup する |
| `deploy-and-google-notion-smoke` | 専用 Worker を deploy し、Google event を既存の適用処理で Notion 内部 DB へ反映して検証後、両資源を cleanup する |
| `deploy-and-qa-notification-smoke` | 専用 Worker を deploy し、所有Q&A pageの初回抑止と更新通知を検証後、Notion pageとDiscord messageをcleanupする |
| `deploy-and-qa-normal-smoke` | 空の専用Q&A DBに3件を作り、通常HTTPハンドラによる全件取得・採番・共有cache・通知・重複抑止と回収を検証する |
| `deploy-and-reminder-normal-smoke` | 空の専用Guildへ4予定を作成し、通常HTTPハンドラの全件取得・2件選別・共有KV・通知・別HTTP重複抑止を検証して回収する |
| `deploy-and-reminder-smoke` | 専用 Worker を deploy し、所有 Scheduled Event の前日通知と重複抑止を検証後、Discord event と message を cleanup する |
| `deploy-and-notion-cleanup-smoke` | 専用 Worker を deploy し、所有する期限到来・将来日時の Notion page だけで期限判定と interval guard を検証後、両 page を cleanup する |
| `deploy-and-webhook-simulation-smoke` | 専用 Worker を deploy し、共通Webhook ingressのtoken拒否・message重複抑止と、所有Google eventの差分取得・Notion反映を検証後、両資源と重複状態をcleanupする |
| `deploy-and-webhook-delivery-smoke` | 専用 Worker を deploy し、run所有の短命watchを作成してGoogleの初回`sync`通知到達を確認後、watchを停止する |
| `deploy-and-webhook-change-smoke` | 専用 Worker を deploy し、所有eventの更新でGoogleの実`exists`通知を発生させ、共通dispatchからその1件だけをNotionへ反映後、watch、dedupe、event、pageをcleanupする |

復旧モードでは既存run IDを必須とし、他モードへの指定を拒否する。一覧取得で適用前に停止したことがstageから確定し、checkpointもNotion page IDもない旧delta記録では、Notion再検索で0件を確認した後に未作成として回収を完了できる。適用開始の痕跡がある場合はdirtyを維持する。

書き込みモードは、各 `seed_fixture`、`trigger_sync`、または所有資源限定の `trigger_job` の監査開始記録がある service / scenario だけを run ID 付きで cleanup する。実行 CLI 内の cleanup に加え、workflow の `always()` step でも一時失敗を最大3回再試行する。所有権不一致、旧 manifest、対象 fingerprint 不一致は再試行せず、他 run の資源を削除しない。

実行前に `e2e` Environment に Secret 5件が設定済みで、variableが0件であることを値を表示せず確認する。Worker URLとそのfingerprintもActionsログでマスクするためSecretとして扱う。設定helperは同名の旧variableがあればSecret登録後に削除する。Google、Discord、Notion の実行時 Secret は Cloudflare Worker だけに保持し、GitHub Actions へ複製しない。

artifact は JUnit XML、マスク済み MCP 監査要約、run manifest だけを14日保持する。run manifest の Worker URL、version、watch、外部資源は SHA-256 fingerprint または真偽値であり、生の識別子、token、request / response 本文を保存しない。

Google→Notion モードは、専用 Calendar に一意な event を作成して読み戻し、現行の `apply_google_events` へ渡し、専用 Notion 内部 DB に作られた page の内容を確認する。外部 Notion DB が空、`DISCORD_SYNC_ENABLED=false`、既定の Notion プロパティ名であることを事前に強制し、適用処理には一時状態を渡すため、同期対応表と再試行キューを KV へ保存しない。Google 認証 token の取得・更新に伴う認証 cache はこの制限の対象外である。

Google→Discord モードは、専用 Calendar に一意な event を作成して読み戻し、現行の `_sync_to_discord` へ1件だけ渡し、専用 Guild に作られた Scheduled Event を確認する。通常設定の `DISCORD_SYNC_ENABLED=false` は維持し、この関数呼び出しだけを一時的に有効化する。Notion、同期対応表、再試行 queue は使用しない。作成応答を失った場合は run marker で一意に再探索し、0件または複数件なら clean と推測せず dirty を維持する。

Discord→Notion モードは、専用 Guild に一意な Scheduled Event を作成して読み戻し、現行の `_sync_discord_event_upsert` へ1件だけ渡し、専用 Notion 内部 DB に作られた page を確認する。`DISCORD_TO_GOOGLE_SYNC_ENABLED=false`、外部 Notion DB が空、既定の Notion プロパティ名であることを事前に強制し、通常の Discord snapshot / queue と作成通知は使用しない。作成応答を失った場合は run marker または Discord event ID で一意に再探索し、所有権が未解決なら dirty を維持する。

Discord差分モードは、専用Guildへrun marker付きeventを1件作成し、通常同期と同じ一覧取得と `_apply_discord_event_diff` を使用する。取得結果からevent ID、Guild、名前、run marker、期待する内容が一致する1件だけを適用し、初回作成、変更なし、説明更新と同じNotion pageへの反映を確認する。更新・削除時のアプリケーション再検索でも既存page IDとの一致を必須とし、検索失敗・ID不一致では下流操作を止める。snapshot / queueはrun所有の1資源に限定し、各差分処理後に `discord_delta` manifest内の `delta_checkpoint` へ一括保存する。次の差分処理前には保存済みの組を読み直し、run ID・対象fingerprint・event IDとrevisionの一致を確認する。通常KVと作成通知先は差分処理へ渡さない。外部資源は独立した `discord_delta` manifestで所有し、run ID・対象fingerprintの一致後だけcleanupする。

続いて所有eventをキャンセルする。現行コードどおり、一覧に残る場合は更新としてNotion pageを維持し、一覧から消える場合は削除差分としてarchiveする。観測した分岐は `delta_cancel_listed` または `delta_cancel_missing` に残す。キャンセル応答の所有情報とstatusを確認できない場合は差分適用を開始しない。その後、残存eventを明示削除し、個別GETの404と一覧からの消失を両方確認してから削除差分を適用する。Notion pageのarchiveをcleanup前に読み戻し、次のポーリングで再削除が起きないことも確認する。応答喪失や検証失敗をcleanup成功で上書きせず、failed_cleanまたはdirtyを残す。通常処理が持つ「一覧から消えた完了eventを削除扱いにしない」判定は維持する。

queueの更新・削除失敗後の再試行は、同じメモリstorageを引き継いでStateStore・DO・差分stateのPythonオブジェクトを作り直すローカルテストでも確認する。checkpointはsnapshot / queueの組を1回のstorage書込みで保存し、競合revision・別run・別資源・上限超過を拒否する。fixture側の管理記録更新では保持し、所有資源のcleanup成功時に消去する。dirty時のstatusにもsnapshotやqueue内のraw IDは公開しない。キャンセル後の一覧の両応答形、完了eventの保護もローカル代替APIの回帰テストで確認する。1回の実サービス実行で観測できるキャンセルの一覧応答は1分岐であり、両分岐を実証したとは扱わない。

Discord一覧取得のHTTP 429は、有限・非負・10秒以内の `retry_after` に従い最大4回まで試行する。MCPはDiscord差分の書込み時に期待version tagを送り、Workerは副作用前に不一致を拒否する。この拒否だけは最大20回・3秒間隔で再送する。

Discord差分の手動workflowは初回deployでversion IDのSHA-256を取得し、更新完了後に同run IDのまま専用Workerを再deployする。再deploy前は `delta_updated`・同run・旧versionの一致と他scenario / serviceのclean状態を確認する。再deploy後は異なるversion IDの反映を待ち、後続の更新再送・続行・完了再送へ新しいfingerprintを送る。Workerは `X-E2E-Version-ID-SHA256` とtagを外部操作前に照合し、同tagの旧versionへの到達も拒否する。監査とmanifestの `version_sha256` はdeployでは観測値、trigger_syncでは要求した値を表し、再deployの `previous_version_sha256` は旧versionを表す。fixtureは1組、deployは2回であり、DO bindingとmigrationは変更しない。この試験は異なるデプロイversionへの続行を対象とし、DOプロセスの強制再起動や任意の書込み位置でのクラッシュ復旧は証明しない。 2026-09-11の[実行34593390627](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34593390627)では2つのversion IDと各要求の指定値をartifactで照合し、全6回のcheckpoint、cleanup、dirty=falseまで確認した。

手動workflowは `trigger_sync` の `sync_phase` を `prepare → advance（応答本文を破棄）→ advance（再送）→ resume 2要求を並行送信 → resume（再送）` の順で実行する。更新後の再送は `updated`・`dirty=true`、完了後の再送は `already_completed`・`dirty=false` を必須とし、不一致時も同runだけをcleanupする。監査JSONLとmanifestのoperationには許可した固定値だけを `execution_status` として記録する。`/admin/e2e/discord-delta-sync/prepare` は作成・読戻し完了後に `status=prepared`、`dirty=true` を返す。`/advance` は無変更・説明更新・Notion読戻しを確認してrevision 3と `delta_updated` を保存し、`status=updated`、`dirty=true` を返す。`/resume` は残りのキャンセル・削除・cleanupを実行する。従来の `prepare → resume` と一括実行も維持する。

最初のadvanceにはMCPの `response_mode=discard_after_headers` を指定する。同modeはversion指定付きのDiscord差分advanceだけに許可し、HTTP 200のヘッダーを受信した後に本文を読まずstreamをcancelする。MCPは `worker_response_discarded`・`response_discarded=true` を失敗として返し、本文由来のstatus・stagesを利用しない。dirtyは不明を示すnullとする。通常の通信失敗、Workerの非200応答、cancel失敗はこの注入成功として扱わない。workflowは注入結果を確認してから別のstatus取得で更新完了checkpointを確認し、再deploy後に通常のadvanceを再送する。

監査JSONL・manifestには注入が実行されたことを真偽値 `response_discarded` で記録する。成功runでも応答破棄のoperationは `ok=false`・HTTP 200として残り、同時resumeの期待409も別に残る。これはMCP側で本文未読を強制する障害注入であり、実際の回線断、Workerの途中停止、処理完了前の中断を起こした証拠ではない。

2026-09-11の[実行34597932061](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34597932061)では、最初のadvanceがHTTP 200・`ok=false`・`response_discarded=true`・`worker_response_discarded` として記録された。別versionへの再deploy後のadvanceはupdatedとなり、同時resume、完了再送、全6回のcheckpoint、cleanupと全資源dirty=falseを確認した。artifactとJUnitを独立取得して照合した。意図した失敗記録は応答破棄1件とロック拒否1件だけだった。

同時resumeの2要求は同run・同versionを指定し、両方の終了を待つ。片方がHTTP 200・dirty=false・通常完了、もう片方がHTTP 409・`e2e_lock_unavailable` の場合だけ成功とする。両方成功、両方拒否、完了済み再送しか観測できない場合、異なるエラーや通信失敗は成功扱いにしない。拒否側を自動再送せず、その失敗を監査・manifestへ保持するため、成功runでも期待した409のoperationが1件残る。両要求が終了してから完了再送・所有状態確認・cleanupへ進み、検証失敗時も両要求終了後に同runだけを回収する。

2026-09-11の[実行34594293913](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34594293913)で同時resumeを実サービス検証した。監査JSONLのresumeは開始・開始・終了・終了・開始・終了の順で、並行2要求のHTTP 200 / 409と、その後のalready_completed応答を独立照合した。期待した409以外のoperationは成功し、全6回のcheckpoint・cleanup・全資源dirty=falseを確認した。

HTTP入口の同期ロックがDO claimより先に作用する。実E2Eの対象は入口ロックによる同時要求の拒否であり、DO claimそのものの競合はローカル代替APIで別に確認する。ローカルでは続行側をclaim後に一時停止し、HTTP入口の拒否側に外部API呼出しがないことと、同revisionを読んだ別要求のDO claim拒否・更新と削除の重複防止を検証する。

各続行は同じrun・対象・保存内容・所有pageを再確認し、DOで保存段階とrevisionが一致する場合だけ `delta_resuming` を取得する。更新完了の保存は取得したclaimとrevision 3を確認し、検証結果と段階を一括で保存する。遅延した前段階の保存要求では次段階のclaimを解除できない。更新完了後の `advance` 再送は所有資源の読戻しだけを行い、説明更新を繰り返さない。成功済みの同runへの `advance` / `resume` HTTP再送は外部操作なしで `already_completed` を返す。準備・更新完了として保存できた境界だけが続行対象であり、外部書込み中や段階保存前の中断はdirtyとしてcleanupする。

更新完了境界の追加はローカル代替APIで検証した。Worker・StateStore・DOのPythonオブジェクト再作成、応答喪失後の再送、古いclaim・revisionの拒否、読戻し・保存失敗後のcleanupを確認した。2026-09-11の[実行34588410907](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34588410907)では3リクエスト構成の更新後再開、全6回の差分・checkpoint、cleanup、dirty=false、Worker version tag一致を実サービスで確認した。さらに[実行34589842665](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34589842665)で更新後・完了後の明示再送を含む5リクエストと固定応答status、全6回のcheckpoint、cleanup・dirty=falseを実サービスで確認した。実際の応答喪失や同時実行競合は起こしていない。オブジェクト再作成はローカル検証のみで、実Worker再起動は含まない。以下の実行34581609741は従来の2リクエスト構成の成功証跡である。

実Worker再起動を伴う復元、共有snapshot / queueの永続化、Guild全件への適用、Google反映、実Cronは未確認である。2026-09-11の[実行34581609741](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34581609741)で別HTTPのprepare / resume、全6回の差分・checkpoint処理、自己cleanup、dirty=false、version tag一致を実環境で確認した。実際に観測したのはキャンセル後に一覧から消える分岐であり、一覧に残る分岐はローカル検証のみである。Discord statusの定義と変更操作は[公式API仕様](https://docs.discord.com/developers/resources/guild-scheduled-event)を参照する。


Discord→Google モードは、専用 Guild に一意な Scheduled Event を作成して読み戻し、現行の `_sync_discord_event_upsert` へ1件だけ渡し、専用 Calendar に作られた event を Discord event ID の private extended property で検索して内容を確認する。通常設定の `DISCORD_TO_GOOGLE_SYNC_ENABLED=false` は維持し、この関数呼び出しだけを有効化する env view では内部・外部 Notion DB を空にする。通常の Discord snapshot / queue と作成通知は使用しない。Google 認証 token の取得・更新に伴う認証 cache は更新され得る。作成結果を確定できず検索結果も0件の場合は clean と推測せず dirty を維持する。

QA通知モードは、専用 Q&A DB に run marker付きの未回答pageを1件作り、実行内cacheで初回通知が抑止されることを確認する。pageの質問を更新して読み戻した後、実行内cacheに更新前markerを保持し、通常ジョブと共通の `_run_qa_notification_pages` へその1件だけを渡して、専用Discordチャンネルに作られたmessageを読戻す。Notionの更新時刻が即時更新の前後で同値の場合は、run内だけの旧markerでcache missを作る。共有KVの `qa_cache`、Q&A DB全件取得、質問番号補完は使用しない。作成応答を失った場合はrun markerで再探索し、所有権が未解決ならdirtyを維持する。

前日リマインドモードは、現在時刻から24時間後の通知ウィンドウ内に開始する run marker 付き外部 Scheduled Event を専用 Guild へ1件作成して読み戻し、通常ジョブと共通の `_run_reminder_events` へその1件だけを渡す。専用チャンネルの message 本文、対象 role だけを許可した mention、実行内 cache 更新を確認し、同じ event を再度渡して message が増えないことを検証する。共有 KV の `reminder_cache`、Guild の通常 event 一覧処理、実 Cron は使用しない。event と message の作成応答を失った場合は run marker で再探索し、所有権が未解決なら dirty を維持する。

Notion cleanup モードは、専用内部 DB に run marker が異なる期限到来 page と将来日時 page を1件ずつ作成して読み戻し、通常ジョブと共通の `_run_auto_clean_pages` へその2件だけを渡す。期限到来 page だけが archive され、将来日時 page が残り、同じ時刻の2回目は interval guard で skip されることを確認する。fixture日時は分境界へ揃え、Notionによる `Z` とUTC offset等の表記正規化を許容して、RFC 3339上の同一時刻として比較する。実行時刻は probe 内状態へ閉じ込めるため、内部 DB の通常全件取得、共有 KV の `cleanup:last_epoch`、実 Cron は使用しない。作成応答を失った場合は page ごとに異なる run marker で再探索し、所有権が未解決なら dirty を維持する。

Webhook simulation モードは、専用 Calendar に run marker 付き event を1件作成し、通常Workerと共通のWebhook ingress handlerへ内部requestを渡す。誤ったchannel tokenでは重複状態もdispatchも開始せず、正しいtokenの1回目だけがGoogle差分取得と同期dispatchを通り、同じchannel IDとmessage numberの2回目はDurable Objectで抑止される。取得結果からevent IDとrun markerが両方一致する1件だけを`apply_google_events`へ渡し、専用Notion内部DBのpageを確認して両資源とrun所有の重複状態をcleanupする。同期cursor、最終実行時刻、最終結果、Google認証cacheはrequest内状態へ閉じ込め、共有KVの対応表とqueueも更新しない。このモードはGoogleから`/gcal/webhook`への実配信、watch channel作成、実Cronを検証しない。

Google Webhook実配信モードは、専用Calendarにrun所有channel ID、固定HTTPS callback、channel token、有効期間600秒を指定して`events.watch`を実行する。Googleの初回`sync`通知だけを専用callbackで受け、watch応答との順序にかかわらず同じresource IDへ原子的に紐付けた後、`channels.stop`で直ちに停止する。通常の同期dispatch、共有KV、`gcal_watch_state`、Google認証cacheは変更しない。停止後のartifactはchannel / resource IDとcallback URLをSHA-256 fingerprintだけで保持する。このモードは変更起因の`exists`通知、通常のWebhook同期、watch renew、実Cronを保証しない。

Google変更起因Webhookモードは、専用Calendarにrun marker付きeventを作成後、600秒のwatchを登録し、初回`sync`を確認してからeventを更新する。Googleが実際に送る`exists` callbackは共通Webhook ingressと同期dispatchを通るが、Durable Objectで最初の1通知だけをclaimし、Google差分結果からevent IDとrun markerが一致する1件だけを`apply_google_events`へ渡す。Notion pageの内容と所有権を確認後、watchをevent削除より先に停止し、run所有dedupe、page、eventを回収する。同期cursor、最終時刻、最終結果、Google認証cacheとNotion対応表はrequest内へ閉じ込め、共有KVと`gcal_watch_state`は更新しない。このモードは通常watchのrenew、共有cursor、全Calendarの全件適用、Discord反映、実Cronを保証しない。

MCP の `trigger_sync` は固定 `scenario` 列挙に応じ、`/sync/all` ではなく `/admin/e2e/google-notion-sync`、`/admin/e2e/google-discord-sync`、`/admin/e2e/discord-notion-sync`、`/admin/e2e/discord-google-sync`、`/admin/e2e/discord-delta-sync` のいずれかを呼ぶ。Discord差分は `sync_phase` に `prepare` / `advance` / `resume` を指定すると同path配下の固定経路を使い、省略時は従来の一括実行を維持する。通常KV補助シナリオは `/admin/e2e/discord-state` を使い、`prepare` で保存し、`resume` で `/verify` を呼ぶ。外部サービスを使う差分モード以外が確認するのは source event の作成・読取からアプリケーション適用処理を経た下流資源作成までであり、Google / Discord の差分取得、同期 cursor / snapshot / queue、全体同期、実 webhook / Cron 配信、Playwright によるブラウザ表示は保証しない。

`trigger_job` の `qa_check`、`reminder`、`cleanup` は、それぞれ所有資源限定の `/admin/e2e/qa-notification`、`/admin/e2e/reminder`、`/admin/e2e/notion-cleanup` を呼び、通常の `/jobs/qa-check`、`/jobs/reminder`、`/jobs/cleanup` は呼ばない。`trigger_webhook` は内部simulation用route、`trigger_webhook_delivery`は初回実配信用route、`trigger_webhook_change`は実`exists`通知と所有event限定dispatch用routeをそれぞれ呼ぶ。Googleからのcallbackだけが`/gcal/webhook`へ到達し、初回配信モードは`sync`の所有確認だけ、変更起因モードは最初の`exists`だけを共通dispatchへ渡す。run-all、共有状態と全件適用を伴う通常の同期・Webhook同期・ジョブ route は、下流資源と共有状態を run ID で所有・回収できるまで実行しない。E2E Worker は `E2E_ORCHESTRATED_WRITES_ENABLED=false` で通常 route を `404` にし、preflight はこの既定拒否と11個の所有資源限定 scenario route の有効状態を別々に確認する。残作業は [GitHub Issue #17](https://github.com/lycanthr0pes/IE_Event_Bot_fork/issues/17) で追跡する。


固定2件の `discord_batch` は、作成前から2組の外部資源を同じDO manifestで所有し、通常StateStoreのrun・scope別KVへsnapshot / queueを保存する。`POST /admin/e2e/discord-batch` でDiscord event 2件を作り、通常差分処理へ上限1件で渡す。Notion page 1件とqueue残件1件を別HTTPの `/verify` で確認し、`/advance` で残り1件だけを適用する。再度の `/verify` でpage 2件とqueue空を確認してから `/cleanup` する。認証・POST・globalロックは全経路で必須で、cleanup以外はrunとWorker version tagも照合する。`E2E_DISCORD_BATCH_ENABLED=true` と `E2E_STATE_SCOPE` が必要である。

MCPは `trigger_sync(scenario="discord_batch", sync_phase="prepare" / "resume" / "advance")` と `cleanup_run(service="discord_batch")` を使う。手動モード `deploy-and-discord-batch-smoke` はprepare / advanceを各1回に限定し、各読戻しで同run・dirty=true・HTTP 409の `discord_batch_not_ready` だけを3秒間隔・最大25回まで待つ。初回残件の確認前にadvanceせず、advanceを再送しない。cleanupは外部資源ごとの完了をDOに記録し、未完了だけを再試行する。外部資源の回収と固定2キーの削除が完了するまでdirtyを維持し、最後はIDを除いたfingerprintだけを残す。

`tests/test_e2e_discord_batch_probe.py` は固定2件の上限・残件処理、別HTTPの状態復元、古いKVの2回目読込による処理済みイベントへの再適用拒否、保存・削除・部分回収の失敗、所有権変更、再検証失敗後の成功判定取消しを代替APIで確認する。Google同期、作成通知、TTL超過、実サービスでの保存失敗注入は含まない。2026-09-12（JST）の[実行34614558706](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34614558706)でprepare→残件確認→advance→最終確認→cleanupが成功した。各verifyは1回、prepare / advanceも各1回である。監査とmanifestの7操作、初回上限・残件読戻し・残件適用・最終読戻し・KV回収の各200、Discord削除204とNotion archive 200を各2件、version・run一致、outcome=passed、全資源dirty=falseを独立照合した。KV遅延による待機は発生していない。cleanupは外部API応答とKV deleteの完了を確認し、全拠点の削除反映は保証しない。

通常ポーリングへの接続では、`discord_batch` の初回・advanceで `run_discord_notion_poll_sync` を呼ぶ。同じ一覧API取得を通し、状態読込み前にDOで所有する2件を選別する。所有ID・run marker・guild・初期内容が一致し、重複と欠落がないことを検証する。一覧順が変わってもDOに記録した順序で処理し、他のイベントは差分処理・KV・Notion反映へ渡さない。`batch_first_poll` / `batch_remaining_poll` を成功証跡へ記録する。通常の呼出しは選別・適用runnerを指定せず、従来どおり全件を扱う。

追加のローカルテストは、有効な他イベントの混在と逆順、初回・advanceそれぞれの一覧欠落・重複・内容変更・不正要素・HTTP失敗を検証する。拒否時にはNotionとKVへ書き込まず、dirtyを保持して回収する。Google同期・通知・手動/Cronの通常HTTP入口はこのシナリオへ未接続で、実行34614558706は接続前の差分処理直接呼出しの証拠である。2026-09-12（JST）の[実行34615847619](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34615847619)では通常ポーリング経由が成功した。初回・残件のpoll各200、上限・残件読戻し・残件消化・最終読戻し・KV回収各200、Discord削除204とNotion archive 200各2件をartifactで独立照合した。監査7操作、Worker version・run一致、outcome=passed、全資源dirty=false、JUnit 410件・失敗0を確認した。verifyは各段階1回で、KV読戻しの再試行は発生していない。


`discord_batch_google` は、同じ通常ポーリングと上限1件・別HTTP残件処理をGoogleとNotionへ接続する別シナリオである。`POST /admin/e2e/discord-batch-google` と `/verify`・`/advance`・`/cleanup` を使い、`E2E_DISCORD_BATCH_GOOGLE_ENABLED=true` と専用scopeが必要になる。通常のGoogle同期設定はfalseのまま、当該呼出しのenv viewだけをtrueにする。通知先と通常state bindingは隠す。

GoogleイベントIDは各fixtureのrunからSHA-256で固定し、作成前からDOで保持する。Googleの[イベント作成仕様](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)に従ったIDであり、作成直前のGETが404の場合だけPOSTへ進む。通常の単一イベント適用に追加したcallbackでGoogle・Notionそれぞれの作成着手をDOへ記録する。Google作成応答のID不一致は失敗とし、NotionのGoogleイベントIDとGoogleのDiscord参照・内容・時刻を別HTTPで照合する。既存IDとの衝突は採用しない。

Google認証は各HTTPで既存の認証解決を使うが、通常KVのtoken cacheを読書きしない。解決したtokenはリクエスト内だけで再利用する。cleanupは既知のGoogle IDの所有をGETで確認して削除し、Notion・Discordと固定2KVキーも回収する。404/410は対象なしとして扱い、所有権不一致・削除失敗・KV削除失敗ではdirtyを保持する。全外部資源とKVの回収後だけcleanとし、固定IDを含む集約fingerprintを残す。

MCPは `trigger_sync(scenario="discord_batch_google", sync_phase="prepare" / "resume" / "advance")` と `cleanup_run(service="discord_batch_google")` を使う。手動モードは `deploy-and-discord-batch-google-smoke`。専用Workerを1回deployし、Discord・Notion・Googleを各2件まで作る。prepare / advance各1回、各verify最大25回、同run・dirty=true・409の `discord_batch_google_not_ready` だけを待つ。作成の再送と任意位置からの自動再開は行わない。

`tests/test_e2e_discord_batch_google.py` は対応ID・残件、Google/Notion作成応答の喪失、既存ID衝突、保存失敗、Google削除失敗、所有権/内容/対応ID変更、認証失敗後の成功判定取消しを代替APIで検証する。これらの障害注入はローカル限定で、実際の回線断・Worker停止は追加対象外である。Googleの通常cursor・対応表・全Calendar取得、作成通知、実Cron、TTL超過はこのシナリオへ含めない。

2026-09-12（JST）の[実行34619150601](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34619150601)で `discord_batch_google` が成功した。実装commit `5d4a12bc40950b3e5a844db3355d04602edf119e` に対し、初回・残件の通常ポーリング、Google/Notion対応IDの読戻し、上限1件・残件消化・最終読戻しを確認した。prepare / advanceは各1回、verifyは各段階1回である。artifactの監査7操作、Google/Discord削除204とNotion archive 200各2件、KV回収200、Worker version・run一致、outcome=passed、全資源dirty=false、JUnit 431件・失敗0を独立照合した。KV読戻し再試行は発生していない。回収確認は各API応答とKV delete完了の範囲で、全拠点の削除反映を保証しない。

## テスト構成

| ファイル | 対象 |
| --- | --- |
| `tests/conftest.py` | `workers` ランタイムの最小代替と外部通信の遮断 |
| `tests/fakes.py` | HTTP Request、Workers KV、Durable Object storage / namespace |
| `tests/test_entry.py` | HTTP 認可の fail-closed、Webhook token、クールダウン、排他、Webhook 重複抑止 |
| `tests/test_google_watch.py` | channel token の必須・最大長・登録、旧 watch と token 変更時の更新、外部エラー本文の非公開 |
| `tests/test_state.py` | KV 読み書き、重複抑止、Durable Object 優先経路 |
| `tests/test_sync_lock_do.py` | ロック競合・解放、Webhook 重複レコードの期限 |
| `tests/test_e2e_google_webhook_change_probe.py` | 実`sync` / `exists` callback、所有event限定dispatch、後続通知抑止、watch先行cleanup |
| `tests/test_e2e_discord_delta_resume.py` | 別HTTPリクエストとオブジェクト再作成による準備・続行、所有権読戻し、続行claim、成功後の再送、中断後のcleanup |
| `tests/test_e2e_discord_delta_state.py` | run単位のsnapshot / queue一括保存、オブジェクト再作成後の再試行、revision・所有境界、保存失敗とcleanup時の消去 |
| `tests/test_e2e_discord_delta_probe.py` | 所有イベントの一覧取得・作成・無変更・更新・キャンセル・削除、archive読戻し、状態分離、失敗queue再試行、完了event保護、応答喪失とcleanup再実行 |
| `tests/test_sync_queues.py` | Google / Discord 同期の件数制限、失敗と残件の繰り越し |
| `tests/test_e2e_entry.py` | E2E route allowlist、run ID、status のマスキング、Cron 無効化 |
| `tests/test_e2e_*_probe.py` | 外部通信を差し替えた CRUD、サービス間適用、QA通知、前日リマインド、Notion期限cleanup、Webhook simulation、Google初回Webhook配信、DO manifest、cleanup、応答喪失、rate limit |
| `tests/test_jobs.py` | Q&A更新通知、前日リマインド、Notion期限cleanupの共通処理と実行内状態 |
| `tools/e2e_mcp_server.test.mjs` | MCP tool allowlist、接続先 fingerprint、承認、skip 判定、run manifest |
| `tools/run_e2e_workflow.test.mjs` | workflow 順序、途中失敗時 cleanup、再試行、監査対象、evidence の固定エラー |

## 外部通信の扱い

`tests/conftest.py` が Cloudflare Python Workers の `fetch` を、常にテスト失敗にする関数へ置き換える。外部 API を扱うテストは、対象モジュールの通信境界を `monkeypatch` で明示的に差し替える。

テストへ実トークン、サービスアカウント、実 DB ID、実チャンネル ID を渡してはならない。fixture には `test-token` など用途が明らかなダミー値を使う。

## 非同期コードの扱い

追加依存を増やさず、各テストは `asyncio.run()` で非同期処理を実行する。テスト内でイベントループを共有する必要が生じた場合だけ、非同期テスト用依存の追加を検討する。

## このテストで保証しないこと

ローカルテストは次を確認しない。

- Cloudflare の Python Workers、Workers KV、Durable Objects の実ランタイム互換性
- `workers/wrangler.jsonc` と Cloudflare 管理画面側バインディングの一致
- Discord、Google、Notion の現在の API 仕様、権限、レート制限、データ内容
- Cron、デプロイ済みWorker上でのGoogle watchとWebhook実配信（専用workflowの実行証跡とは別）
- デプロイ後の疎通、性能、可用性

これらはローカル単体テストと分け、認証情報と実行許可を確認したうえで preview または実環境の検証として扱う。

## テスト追加時の方針

- 1テストにつき、判定したい振る舞いを1つに絞る。
- 時刻、UUID、外部 API 応答は必要な境界で固定する。
- 成功だけでなく、失敗、再試行、件数上限、空データを確認する。
- 本番コードの内部実装ではなく、返り値と保存状態を優先して検証する。
- テスト後に `ruff check .`、`pyright`、`git diff --check` も実行する。


### 通常ポーリングの作成通知・リアクション再試行

`discord_batch_notification` は、Discord Scheduled Event 2件・Notion page 2件・通知message 2件を上限とする別シナリオである。`E2E_DISCORD_BATCH_NOTIFICATION_ENABLED=true`、専用の `E2E_STATE_SCOPE`、既存の通知channel・role設定が必要になる。準備時にguild・channelの所属・role・Notion DBを検証し、各HTTPでrunと対象fingerprintを照合する。Google反映はこのシナリオでは無効のままとする。

`POST /admin/e2e/discord-batch-notification` と `/verify`・`/advance`・`/cleanup` を使う。処理順は次のとおり。

1. prepare: 通常一覧取得から所有2件を選別し、上限1件でNotion反映・通知投稿を行う。1件目のリアクションだけはAPI呼出し前に固定の失敗を返す。KVには投稿済みID付き `notify` と、2件目の通知先付き `upsert` が残る。
2. verify: 1件目のpage・message・リアクション未付与、2件目の未作成、snapshotとqueue残件2件を別HTTPで照合する。
3. advance: 通常ポーリングから1件目のリアクションだけを再試行する。Notion再作成とmessage再投稿を拒否し、`retry_drained` を返す。
4. verify: 同じmessageへのリアクション付与と、2件目だけのqueueを確認し、`batch_retry_verified` に進む。
5. advance: 2件目のNotion反映・通知投稿・リアクション付与を行い、`drained` を返す。
6. verify: page・message各2件、リアクション、snapshot・queue空を確認する。
7. cleanup: 所有messageを削除しGET 404まで確認する。イベント・page・固定2KVキーも回収してからcleanにする。未回収があればdirtyを保持し、完了済みmessageの削除は繰り返さない。

MCPは `trigger_sync(scenario="discord_batch_notification", sync_phase="prepare" / "resume" / "advance")` と `cleanup_run(service="discord_batch_notification")` を使う。手動モードは `deploy-and-discord-batch-notification-smoke`。Workerを1回deployし、prepareを1回、advanceを各段階1回、verifyを各段階最大25回実行する。待つのは同run・dirty=true・HTTP 409の `discord_batch_notification_not_ready` だけで、投稿・advanceを再送しない。workflow末尾の `always()` cleanupも監査からこのシナリオを回収する。

投稿前にDOへ着手と本文SHA-256、投稿後にmessage IDを保存する。本文に含まれるrun固有のイベント名・channel・role・本文hashを検証する。投稿応答またはID保存が失敗した場合は再投稿せず、cleanupで直近50件から完全一致するイベント名の行を検索する。候補が複数、候補不明、本文や通知先が不一致なら削除せずdirtyを残す。検索範囲外も未回収扱いになる。

`tests/test_e2e_discord_batch_notification.py` は、別HTTPでの通知再試行と繰越、投稿応答・DO保存・KV保存の失敗、回収失敗と再回収、所有権や本文の変更、古いqueueの再読込、認可・version・gate、再検証失敗後の成功取消しを代替APIで検証する。初回の失敗はE2E runnerの固定注入であり、Discord側の障害を発生させる試験ではない。実Cron・TTL超過は未検証で、項目10の追加対象外は維持する。

2026-09-14（JST）の[実行34831533775](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34831533775)で実サービス検証が成功した。fork revision `b95be41d1e7c31f5d707168650f644caa10968c7` を専用Workerへ1回deployし、通知2件、投稿済みmessageへのリアクションだけの再試行、上限1件・別HTTP残件処理・最終読戻しを確認した。prepare 1回、advance 2回、verify各段階1回で、KV読戻し再試行は発生していない。artifactの監査18行・完了9操作とmanifestを独立照合し、`prepared` → `retry_drained` → `drained`、通知削除204・削除後GET 404、Discordイベント削除204・Notion archive 200各2件、KV回収200、2回のcleanup成功、Worker version・run一致、`outcome=passed`、全資源 `dirty=false`、JUnit 487件・失敗0を確認した。回収の確認範囲は各API応答・通知削除後のGET・KV delete完了であり、KV全拠点への削除反映は保証しない。


### 通常同期の共通ロック競合

`sync_lock` は `POST /admin/e2e/sync-lock`、`/verify`、`/cleanup` を使う。手動モードは `deploy-and-sync-lock-smoke`。専用Workerの `E2E_SYNC_LOCK_ENABLED=true`、`E2E_STATE_SCOPE`、KV・DO binding、DOロック有効・クールダウン無効を必要とする。全経路で認証とPOSTを要求し、prepare / verifyはrunとversion tagを照合する。E2E Workerの通常書込みrouteと実Cronは既存どおり無効である。

prepareの1 HTTP内で、手動Discord同期・CronのDiscord同期分岐と共通の `_run_discord_sync`、全体同期の `_run_sync_dispatch` を呼ぶ。各経路を保持側とし、正常完了と同期本体の固定例外の計6 roundを実行する。保持側が実際にglobalロックを取得して本体へ到達した時点で待機させ、3経路すべてを競合側として呼ぶ。単独同期の409、全体同期の `in_progress_skip`（200）、競合側の本体未実行・結果KVへのアクセスなし、保持ownerの維持を確認する。保持側の成功・例外後の解放と、例外後に同経路が成功できることを確認する。各roundの待機は10秒、round全体20秒、HTTP内の処理全体45秒を上限とし、失敗時も保持taskの終了とfinallyを待つ。

同期本体は検査用runnerへ差し替え、Discord・Google・Notionへ通信しない。結果保存は通常StateStoreを通し、run・scope・round別KVへ隔離する。6 round×固定3キーだけを許可し、正常経路が実際に保存するのは合計8キーである。DOには結果本文の代わりにSHA-256を保持する。別HTTPのverifyはKVから読み直し、期待したキーのhashと、それ以外の未作成を確認する。古い値の固定409だけをworkflowが最大25回・3秒間隔で待ち、prepareは再送しない。

制御用DO `e2e:sync-lock-control` のロックが同シナリオの並行prepare / verify / cleanupを拒否する。通常同期が取得するglobalロックとは別であり、その取得・解放を置き換えない。cleanupはglobalロックが残っていれば回収を止め、他ownerを強制解放しない。固定18キーを回収し、制御ロックの解放も読み戻した後でcleanにする。検証成功前に回収した場合は `failed_clean`、成功後だけ `passed` とする。workflowの `always()` cleanupと監査・manifest収集にも接続する。

`tests/test_e2e_sync_lock_probe.py` は実DOロジックと代替KVを使い、6 round、別HTTP読戻し、認証・version・設定拒否、所有情報・hashの差し替え拒否、古いKV、保存・回収・ロック解放失敗、タイムアウト、clean後の再利用拒否を検証する。MCP・workflowでは固定経路、応答不一致の拒否、失敗後の回収、version・outcome照合を検証する。これらの代替APIテストはローカル検証である。実KV・DOの確認は次の専用実行と区別する。別Workerリクエスト間の競合、実Cron配信、外部API適用中の競合、TTL超過は未検証である。

2026-09-14（JST）の[実行34834547224](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34834547224)で、fork revision `f0a342e1965bbd086d4ff2ed3834673a3475a7cc` を専用Workerへ1回deployし、`sync_lock` を実KV・DOで検証した。6 roundの競合拒否・結果保護・成功/固定例外後の解放、別HTTPでの結果8キーのhashと未作成キーの読戻し、固定18キーの回収が成功した。artifactの監査10行・完了5操作とmanifestを独立照合し、6 round・読戻し・KV回収の各200、run内と `always()` のcleanup成功、Worker version・run一致、`outcome=passed`、全資源 `dirty=false`、JUnit 510件・失敗0を確認した。prepare / verifyは各1回で、KV読戻し再試行は発生していない。同期本体は検査用runnerであり、Discord・Google・Notionへの同期と実Cron配信は実行していない。KV回収はdelete完了の確認であり、全拠点への削除伝播を保証しない。


## 状態障害とTTL超過のE2E

`e2e_sync_fault_probe.py` の `sync_faults` は `POST /admin/e2e/sync-faults`、`/advance`、`/verify`、`/cleanup` を使う。`E2E_SYNC_FAULTS_ENABLED=true`、認証、POST、KV・DO、状態scope、ロック有効・KVクールダウン無効を必須とする。prepare / advance / verifyはrun IDと稼働version tagを照合し、cleanupは同run・同対象を確認する。通常入口・実Cronは有効化しない。

固定イベント2件と通常の `_apply_discord_event_diff`、通常StateStoreを使う。外部適用は成功・失敗と呼出し回数だけを返す代替runnerで、外部APIを呼ばない。書込みは所有KVに実行するが、prepare中の読取りは直前の書込みを保持するメモリと固定の古い値で制御する。Cloudflare側の伝播遅延を発生させた検証ではない。KVは強整合・複数キーのトランザクションを提供しないため、この区別を保つ（[Cloudflare KVの整合性](https://developers.cloudflare.com/kv/concepts/how-kv-works/)）。

| 固定ケース | 確認する現行挙動 |
| --- | --- |
| 古いsnapshot | 成功済みイベントを1回再適用する |
| 古いqueueの再出現 | 成功済みイベントを1回再適用する |
| 古い空queueと最新snapshot | 初回の通常処理が保存した残件をsnapshotから復元し、再試行後に2件とも適用する |
| queue保存前の失敗 | 成功した1件を再適用し、2件へ計3回適用する |
| queue保存後の失敗 | 保存済み残件から続行し、2件へ計3回適用する |
| snapshot保存前の失敗 | 保存済み残件から続行し、2件へ計3回適用する |
| snapshot保存後の失敗 | 保存済み残件だけを続行し、2件へ計2回適用する |

保存失敗は各ケース1回だけ、最初の適用成功後に注入する。保存後の失敗はKV putが返った直後の固定例外であり、実サービスの応答喪失ではない。同じキーへの再書込みは1.05秒以上離す。`passed` は上記の期待挙動の確認であり、重複適用の解決を意味しない。`stale_queue_loss` は修正後の残件回復を必須とし、初回失敗後の再適用と次イベントの適用、`pending_lost=false` を照合する。実環境の確認は [E2E-PLAN.md](E2E-PLAN.md) に残す。

TTLケースは手動Discord同期の共通処理を10秒TTLで保持する。DOの時計が期限に達するまで最大65回・0.2秒間隔で待ち、新実行がロックを取った後に旧実行を完了させる。旧実行が409 `sync_lock_lost` となり、結果を書かず、新ownerを解放しないことを確認する。新実行の成功・結果保存・解放まで確認し、失敗時も全taskのfinally完了を待つ。TTLを短くする引数はE2E内部だけから渡し、通常の120秒設定は変更しない。各HTTPのphase処理は50秒を上限にする。上限到達は `sync_faults_phase_timeout` として返す。

通常入口には段階間の期限・所有者確認を追加した。Discord同期後の結果保存、Google取得後の適用開始、適用後のcursor保存、Discord開始、最終時刻・結果保存の前でDOの時刻とownerを確認し、不一致・期限切れ・確認不能なら409で停止する。これは確認とKV書込みの原子的な保護ではなく、同期本体内のqueue保存、実行済み外部書込み、進行中の外部API処理を取り消さない。TTL以内の完了、全書込みの排他、一度限りの反映は保証しない。

DO所有manifestはrun・scope・対象・各ケースの証拠hashを保持する。verifyは8ケースの証拠と状態hashを別HTTPで実KVから読み、未作成キーの不在も照合する。失敗した再検証は以前の成功を無効化する。cleanupは固定48候補キーを削除し、制御DO `e2e:sync-fault-control` の所有ロック解放も読戻した後だけcleanとする。失敗時はdirtyを保持し、再回収できる。globalロックの強制解放は行わない。

MCPは `trigger_sync(scenario="sync_faults", sync_phase="prepare" / "advance" / "resume")` と `cleanup_run(service="sync_faults")` を使う。手動workflow `deploy-and-sync-faults-smoke` は1回deploy・1回prepare・7回advanceと有限のverify待機、version・段階・outcomeの照合、通常と `always()` のcleanup、マスク済み監査収集へ接続する。

ローカルでは実DOロジック・代替KVを使い、8ケース、所有者・hash変更の拒否、認証・version・設定拒否、部分保存・読戻し・回収・解放失敗を検証する。TTL単体テストはDOの時計を進め、手動・Cron分岐・全体同期の旧結果拒否と新owner保護、Google適用後のcursor保護、壊れた時計・statusの拒否を確認する。2026-09-14の[実行34839754885](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34839754885)は `76ac4c8` を専用Workerへdeployし、KV障害7ケースのprepare処理を完了したが、約51.4秒で409 `sync_faults_probe_failed` となった。TTLケースと別HTTP読戻しは未完了である。50秒のphase上限への到達が疑われるが、旧エラー分類だけでは例外種別を確定できない。run内と `always()` のcleanupは200、対象manifestは `failed_clean`・`dirty=false`、全資源clean、version・run・commit一致をartifactで確認した。分割後は[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)で実KV・DO検証が成功した。

分割後はprepareが先頭ケースを1件だけ実行し、advanceが残りを固定順に1件ずつ進める。途中は `partial`、8件完了後だけ `prepared` を返す。DOには着手前の `fault_testing`、確定後の `fault_partial` / `fault_prepared` と証拠hashを保存する。書込み途中の失敗は `fault_testing` に留まり、advanceで再実行・スキップせず回収する。途中でverifyを要求しても不完全として拒否し、確定済みの進捗は壊さない。workflowは書込みを再送せず、7回のadvanceと最後の完了応答を確認してからverifyへ進む。旧版のdirty記録も同run・同対象のcleanupで回収できる。

分割・途中タイムアウト・所有者とversionの不一致・ケース飛越しをローカル検証する。合算60秒の固定遅延モデルで、各ケースを別要求へ分けたときだけ50秒予算内に収まることも確認する。これは実Cloudflareの処理時間の証明ではない。


## 古いqueueによる残件喪失の対策

`discord_retry_state.py` は未処理のupsert・delete・notifyをsnapshotの指紋JSON内の `_pending_sync` にも記録する。通知先と投稿済みmessage IDも保持する。保存は従来どおりqueueを先に書き、続いて観測指紋と残件情報を同じsnapshot値へ書く。次回はqueueを優先し、snapshotだけに残る操作を補完する。同じIDは重複させず、どちらかがupsertなら未適用の可能性を優先し、通知だけの再試行へ縮めない。投稿済みmessage IDはIDのない古い値で消さず、通知先・message IDの矛盾は処理前に拒否する。

比較前に残件情報を指紋から分離するため、通知待ちだけでイベント更新と判定しない。削除待ちはsnapshotに記録を残すが、観測イベントには数えない。成功後は該当する残件情報を除く。既存の文字列指紋とqueue形式は引き続き読める。旧形式snapshotに残件情報がなくqueueも古い場合、失われた操作はこの変更だけでは再構成できない。

`tests/test_discord_stale_queue.py` は、別イベントの失敗queueで上書きされる条件を作成・更新・削除で再現し、件数上限の残件と通知待ちも含めて回復を確認する。通知では同じmessageへリアクションを再試行し、再投稿しない。古いsnapshotと新しいqueueの組合せ、通知先の矛盾、壊れた残件、処理順も検証する。E2EのKV・DO checkpointは埋込残件にも所有権検査を行い、batch適用中のqueueが期待値から変われば外部書込み前に拒否する。

この対策は、片方に保持された残件をもう片方の古い値で捨てないためのものである。両キーが残件作成前の古い値なら未知の操作を復元できず、古い残件の再出現や外部成功後の保存失敗では再適用され得る。KVの複数キーの原子性、一度限りの外部反映、TTL超過中の全書込みの排他を追加保証するものではない。新形式を読まない旧版へ戻す場合は、稼働中の残件を回収・完了させてから状態を確認する。

### 状態障害の保証範囲表

2026-09-14に [test_sync_guarantee_boundaries.py](../tests/test_sync_guarantee_boundaries.py) の8ケースを追加し、通常処理で以下の制限を再現した。古い読取り、保存失敗、応答喪失、時計の進行は固定注入であり、実サービスの障害発生率・伝播時間を測定した結果ではない。テスト成功は記載した挙動の確認を意味し、残る不具合の解消を意味しない。

| 条件 | 現行の結果・保証 | 検証根拠 |
| --- | --- | --- |
| 新形式snapshotに残件が見え、queueだけが古い | 作成・更新・削除・上限繰越・通知待ちを復元する | `test_discord_stale_queue.py`、実KVの固定 `stale_queue_loss` ケース |
| 両キーが残件の見えない古い値 | 削除待ちを復元できない。別イベントの失敗残件を保存すると元の残件が上書きされ、その後の新しい読取りでも戻らない | 追加テスト `test_invisible_delete_pending_cannot_be_recovered[False]` |
| 残件markerのない旧snapshotと古いqueue | 同じ削除待ちの喪失が起きる。旧形式を読めることと、隠れた残件の復元は別の保証 | 同 `[True]`。可視の旧queue互換は `test_discord_notification_retry.py` |
| 成功後にsnapshot・queueがともに空の古い値 | 再び新規と判定し、外部適用と作成通知を重複実行する | 追加テスト `test_stale_empty_state_replays_apply_and_creation_notification` |
| 投稿済みmessage IDが残件に見える | 同じmessageへリアクションだけを再試行し、再投稿しない | `test_discord_stale_queue.py`、実通知E2E |
| 投稿済みだが応答からmessage IDを取得できない | 次回は新しいmessageを投稿するため、一度限りの配信を保証しない | 追加テスト `test_missing_post_response_retries_with_another_message` |
| 通知成功後、queue保存前に失敗 | snapshot保存も未完了なら再適用・再投稿が起きる | 追加テスト `test_queue_save_failure_after_notification_allows_duplicate` |
| 段階間のowner確認時点で期限切れ・別owner・確認不能 | 409で停止し、その段階以降のcursor・最終結果保存を拒否する | `test_sync_lock_expiry.py`、実DOの固定TTLケース |
| owner確認後、結果KV保存の完了前に期限切れ | 新実行が保存した結果を旧実行が上書きし、旧実行も200を返し得る。旧ownerの解放処理は新ownerを解放しない | 追加テスト `test_expiry_during_result_put_can_overwrite_new_result` の手動・Cron分岐・全体同期3ケース |

外部適用・通知とKV保存を一つのトランザクションにはしていない。DOのowner確認とKV保存も不可分ではない。項目3の「保証範囲確定」はこの表の確定を指し、残件の無損失、一度限りの反映、全書込みの排他を完了条件へ追加したものではない。

### 通常Google同期のローカル接続検証

[test_google_sync_pipeline.py](../tests/test_google_sync_pipeline.py) は通常 `_run_sync_dispatch`、`run_google_delta_fetch`、`apply_google_events`、`StateStore` を通す。Google HTTP応答、認証token取得、Notion API補助関数、Discord API境界だけを代替し、DOはローカルで実ロジックを使う。各呼出しで `StateStore` を作り直す。

- 2ページを取得し、上限1件で残件を保存する。次回の2分重複範囲のquery、残件消化、Notion・Discord対応表、同じIDでの更新、取消時のarchive・削除・対応表除去、cursor更新を照合する。
- Notion照会の固定例外でcursorを進めず、失敗分を未処理分より先に再試行する。
- Googleの2ページ目が503なら、1ページ目も適用せず、cursor・対応表・queueを更新しない。

3ケースは通常経路の接続確認である。実サービス用の所有・回収は以下のシナリオへ実装した。全Calendarの実取得、外部DB、全APIの部分失敗、古いqueueと新しい同一イベントの競合、実KV伝播は未検証で、[E2E-PLAN.md](E2E-PLAN.md) 項目4は継続中である。

通常Discord APIの作成・更新失敗でNoneが返ると、従来はGoogle適用が成功扱いになっていた。追加2ケースで失敗を再現し、Discord同期有効時にIDを得られなければ残件へ保存するよう修正した。cursorと最終成功時刻の維持、空の新規取得からの残件回復、既存Notion IDの再利用、Discord同期無効時の互換性をローカルで確認した。削除失敗、Notion ID書戻し失敗、応答喪失後の重複作成はこの修正の保証に含めない。

### 通常Google同期の専用E2E

2026-09-24の追加検証では、[test_google_sync_failures.py](../tests/test_google_sync_failures.py) に通常dispatch・通常StateStore・通常HTTPラッパーを通す21ケースを追加した。Notionの照会・ページ取得・作成・archive・Discord ID書戻し、Discord削除について403・429・503を代替APIから返し、失敗時のcursor/最終成功時刻の不変、残件保存、空の新規取得からの回復を確認する。subrequest上限では件数上限内の未着手分も保持する。Discord削除の404は削除済みとして扱い、Discord由来イベントの取消では元Discord予定を削除しない。修正前は19ケースが失敗した。実サービスの障害、KV保存失敗、応答喪失後の重複、Notion作成直後のページUUID書戻し失敗はこの検証の保証に含めない。

`e2e_google_sync_probe.py` の `google_sync` は、`POST /admin/e2e/google-sync` と `/advance`・`/verify`・`/cleanup` を使う。`E2E_GOOGLE_SYNC_ENABLED=true`、内部認証、run ID、稼働version tagとの一致、KV・DO、専用Calendar・Notion内部DB・Discord guild、共通ロック有効・クールダウン無効を必須とする。cleanupは同run・同対象を確認するが、稼働version tagへの一致を要求しない。

| 段階 | 操作と確認 |
| --- | --- |
| prepare → pending | run由来の固定Google IDで2件作成。通常全ページ取得から所有2件を選び、通常dispatch・適用を上限1件で実行。Notion・Discord各1件と残件1件を保存 |
| verify | 別HTTPで6つのKV値のhash、cursor期待値、queue、対応表、所有予定・ページ・Discordイベントを照合 |
| advance → drained | 前段階の検証済み状態を再確認し、上限2件で残件を消化。重複取得された予定の更新は通常処理に従う |
| advance → updated | 先頭の所有Google予定の説明を更新し、通常差分取得・適用からNotion・Discordへの反映を確認 |
| advance → deleted | 同じ所有Google予定を削除し、通常取得のcancelledからNotion archive・Discord削除・対応表除去を確認 |
| advance → retry_pending | 残った所有Google予定の説明を更新。Notion反映後、所有Discord予定へ `scheduled_start_time="not-a-date"` のPATCHを1回送り、HTTP 400・code 50035を必須とする。通常dispatchの500・残件1件・cursor/最終成功時刻の不変・Notion更新済み/Discord旧内容を確認 |
| advance → retried | 通常取得後の入力を空にして保存queueだけを通常適用へ渡し、同じNotion/Discord IDへの反映・残件0・成功結果を確認 |
| cleanup | Google予定・Notionページ・Discord予定の所有を再確認して回収し、run別KVの固定6キーを削除 |

各advanceの前にverifyを必須とする。専用制御DOロックはphase全体を保護し、通常dispatchは既存の共通同期ロックを使用する。1 HTTPは50秒を上限とする。書込み前に `working` を保存し、途中失敗したphaseは再送せずcleanupへ進む。所有IDが未保存でもrun markerで一意に再発見し、曖昧・所有不一致ならdirtyを維持する。ID衝突で作成していない既存予定は回収しない。検証の再実行が失敗した場合も成功判定を取り消す。

新規runの `api_rejection_enabled=true` はDO manifestで変更不可とする。API拒否前にはDiscord予定をGETしてID・Guild・名前・run markerを再照合する。400以外、または400でもcodeが50035でない応答は試験成功にせず回収へ進む。workflowは `google_sync_discord_invalid_update=400` と `google_sync_discord_rejection_verified=200` を必須にする。旧manifestは従来の固定注入・所有資源回収を維持する。この入力検証エラーは実サービス障害・回線断の観測ではなく、通常の共有名前空間と任意予定の全件適用も含まない。日時の形式は[Discord Scheduled Event仕様](https://docs.discord.com/developers/resources/guild-scheduled-event)に基づく。

初回の[実行35952552380](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35952552380)は空の名前を使う旧要求でAPI拒否段階と回収に失敗した。空名が受理されるモデルをローカルで再現し、新規要求を不正な日時へ変更した。旧runの回収に限り、`api_rejection_enabled=true`・step 4・cleanup段階・2件目の記録済みDiscord ID・Guild・run marker・空の名前のすべてが一致する資源を許可する。未知のIDを名前から推測して削除しない。

`deploy-and-google-sync-recovery` は `recovery_run_id` を必須とし、同runのcleanup段階と稼働tag、他のdirty資源がないことをdeploy前に確認する。修正版を同run IDでdeployした後にgoogle_syncだけを回収し、`failed_clean`、通常とalwaysのcleanup、全資源cleanのpreflight、マスク済みartifactを確認する。fixture作成・同期再開・失敗した試験のpassed化は行わない。

2026-09-24の[復旧実行35955045460](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35955045460)で、`834930f` を同じrun IDへdeployし、空名回収の照合stage 200、Discord削除204、Notion archive 200、Google削除204、KV回収200、`failed_clean`・全資源 `dirty=false` を確認した。監査4行・2操作、run/version/commit・clean checkout、JUnit 642件・失敗0を照合した。元の試験は失敗のままであり、修正版の不正日時要求と再試行の実証とは分ける。

同日の[再実行35959152201](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35959152201)では `d184a2a` をdeployし、不正日時PATCHへのHTTP 400・code 50035の確認stage、通常dispatchの500、cursor・最終成功時刻の保護、別HTTPでqueueだけを処理する回復、全6段階のverify、通常とalwaysのcleanupが成功した。監査30行・15操作、run/version/commit・clean checkout、JUnit 642件・失敗0、`passed`・全資源 `dirty=false` を独立照合した。入力検証エラーの実応答であり、実サービス障害や共有状態の全件適用を検証したものではない。

MCPは `trigger_sync(scenario="google_sync", sync_phase="prepare" / "advance" / "resume")`、`cleanup_run(service="google_sync")` を使用する。手動workflowの `deploy-and-google-sync-smoke` はdeploy 1回、prepare 1回、advance 5回、各段階のverify、稼働version fingerprintとDO段階の照合、通常と `always()` のcleanup、監査収集へ接続する。KVの `google_sync_not_ready`・同run・dirty・409だけを3秒間隔、最大25回待機する。

ローカルでは [test_e2e_google_sync_probe.py](../tests/test_e2e_google_sync_probe.py) の26ケースで全段階、認証・version・設定拒否、古いKV、所有差替え、ID衝突、作成応答喪失、外部作成失敗後の再送拒否、回収再試行、再検証失敗、一覧反映遅延時の取得済みIDによる回収、外部削除後のKV回収失敗からの再試行、JS null/undefinedのKV欠損値、部分反映からの同じIDへのqueue再試行、注入欠落・回復失敗・要件巻戻し・回復後再検証失敗の拒否を確認した。Google・Notion・Discord APIと対象の疎通確認は代替している。2026-09-15（JST）の[実行34862331643](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34862331643)で、所有2件の繰越・消化・更新・削除、各段階の別HTTP読戻し、回収が成功した。prepare 1回・advance 3回・verify 4回で再試行はなく、最長phaseは36.058秒だった。監査22行・11操作、manifest、run・version・commit一致、JUnit 602件・失敗0、`passed`・全資源 `dirty=false` を独立照合した。回収の保証範囲はAPI応答とKV delete完了であり、全拠点への削除伝播完了ではない。任意の外部予定への適用、実Cron、通知、Notion外部DB、部分失敗からの任意位置の自動再開はこのシナリオの対象外である。

追加したretry_pending / retriedは固定注入モデルであり、Discordの実障害・回線断の観測ではない。注入はE2E呼出し内のcallbackだけを使い、モジュール共有状態を書き換えない。通常dispatchの想定500、残件、cursor保護を確認した場合だけシナリオHTTPを200とし、想定外の失敗は回収へ進む。6段階版は[実行34866761198](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34866761198)で成功した。prepare 1回・advance 5回・verify 6回、読戻し再試行なし。注入段階27.218秒・queue回復26.944秒で、通常dispatchの想定500、cursor/最終成功時刻保護、queue回復の固定stageを確認した。監査30行・15操作とmanifest、run・version・commit、JUnit 610件・失敗0、通常とalwaysの回収、passed・全資源dirty=falseを独立照合した。

### Google同期の共有KV・全件モード

全件modeのphase上限は90秒（通常2件modeは50秒）。Google同期routeのMCP→Worker HTTP待機は120秒、workflow→MCPの待機は180秒とし、Workerの制御ロック解放・結果取得を待つ。phaseの上限は通常同期ロック120秒・制御ロック300秒より短い。

`POST /admin/e2e/google-sync/full`、MCPの `trigger_sync(scenario="google_sync", sync_phase="prepare_full")` で開始する。手動workflowは `deploy-and-google-full-smoke` を選ぶ。通常の2件・6段階モードは維持し、全件モードは3件・4段階（pending → drained → updated → deleted）で検証する。advance・verify・cleanupは既存経路を使う。

開始前に、通常Google全ページ取得に有効な予定がなく、Discord予定一覧が空、Notion内部DBの有効ページが空、共有KVの固定6キーが欠損していることを確認する。空文字も既存値として拒否する。Calendarの削除履歴はIDと内容のSHA-256をDO manifestへ保存し、実行中の変更を禁止する。32 KiBのmanifest容量に余地を残すため履歴は100件までとし、超過・ID欠損・重複IDはfixture作成前に拒否する。既存データを消して条件を満たす操作は行わない。専用環境で他の書込み主体がいないことが前提である。

準備で検証予定3件を作成する。通常取得の入力から、開始前に記録したID・内容と一致し、現在もcancelledである履歴だけを除外する。残りの全入力は順序を維持して適用する。未知の予定・新しい削除履歴・既存履歴の内容変更や復元・検証予定の重複ID・不正な内容は適用前に拒否する。既存履歴がAPIから消えたり差分期間外になった場合は欠落として扱わず、fixtureの可視性は引き続き必須にする。履歴を適用・queue・回収対象へ渡さず、今回の検証予定の削除は通常どおり処理する。初回上限1件による残件2件、別HTTPでの消化、更新、削除を通常dispatch・適用処理で検証する。cursor・Notion/Discord対応表・queue・結果はrun prefixのない共有キーへ保存する。最終成功時刻は既存probeと同じKV fallbackを使う。共通DOは通常dispatchの排他とE2E所有記録を担い、通常DOの最終成功時刻を変更する検証は含まない。

全件モードの変更をDOで禁止し、他scenarioのdirty manifestとの併存を拒否する。通常書込みrouteと全Cronのフラグは無効を必須にする。KVへ書く前に値のdigestをDOへ追記し、回収時は記録済みdigestと一致する値だけを削除する。未知の値は保持してdirtyを維持する。KV書込みの応答喪失・削除途中失敗でも記録から回収を再試行できる。削除後の欠損読戻しを必須にするが、KVの全拠点への削除伝播や読取りと削除の原子性は保証しない。

workflowは全入力確認・共有キーの開始時欠損・回収の各stageとversionを照合し、失敗時も既存のcleanupと監査収集を使う。[test_e2e_google_full.py](../tests/test_e2e_google_full.py) では取得順の逆転・複数ページ・全段階、既存データ保護、所有外入力、書込み応答喪失、回収再試行、他scenarioとの競合、設定・manifestの差替え拒否を代替APIと実DOロジックで検証する。実サービスの3件・4段階は以下の実行で成功した。任意件数・繰返し予定など全入力形式への対応を実証したものではない。

2026-09-24の初回[実行35962599536](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35962599536)は `ea6044e` のLocal validationとdeploy成功後、`google_sync_shared_not_empty` で停止した。新規検証資源の作成・共有KV書込み・全件適用には到達していない。cleanupは前回runのclean manifestに対するrun不一致として8回拒否された。監査20行・10操作、version/commit/run一致、全manifest `dirty=false` と前回所有記録の保持、JUnit 659件成功を確認した。今回の全件モードは未検証のままであり、再実行には空のE2E専用共有KVが必要である。

新規KVへの切替後の[実行35963510311](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35963510311)は `952f27f` のdeployと共有キーの空状態検査を通過し、Calendarの開始条件で停止した。`google_sync_calendar_not_empty` は取得失敗にも使われるため、artifactだけでは残存予定・削除履歴・取得失敗を区別できない。新規fixture・共有状態書込みは未実施、全manifestは前回のclean記録を保持した。全件適用の実サービス検証は未完了である。

Calendarの開始条件を切り分ける読み取り専用経路は `POST /admin/e2e/google-sync/inspect`。MCPは `trigger_sync(scenario="google_sync", sync_phase="inspect")`、手動workflowは `deploy-and-google-calendar-check` を使う。専用Workerをdeployしてversion照合後、同じCalendarを `singleEvents=true&showDeleted=true`・全ページで読み、`fields=items(status),nextPageToken` により予定本文やIDを要求しない。結果は `calendar_empty`・`calendar_active`・`calendar_deleted`・`calendar_mixed` の固定分類、API失敗は `google_sync_calendar_http_<status>` として監査へ保存する。診断はKV・manifest・外部予定を書き換えず、回収対象にも追加しない。全件モードの空状態ガードは維持する。Googleの[events.list仕様](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)では `showDeleted=true` により `cancelled` が取得対象になる。

[診断実行35965137690](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35965137690)は `83cd4b3` で成功した。HTTP 200・`calendar_deleted` により、取得対象に通常予定はなく削除履歴だけ残ることを確認した。監査4行・deployとinspectの2操作、run/version/commit一致、全service/scenario manifestが前回と同一で `dirty=false` を照合した。予定・KVは変更していない。当時の実装では削除履歴のないCalendarが必要だったが、その後、記録済みの履歴だけを保護して除外する方式へ修正した。これは診断の成功であり、修正版の全件適用は実サービスでの再検証が必要である。

[実行35968760516](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35968760516)は削除履歴照合・3件作成・初回dispatchを通過したが、旧50秒上限で `google_sync_timeout` となった。監査8行・4操作、run/version/commit一致、今回runの `failed_clean`・全manifest `dirty=false`・共有KV回収成功を照合した。タイムアウトを調整した版の全件4段階は再検証待ち。

[実行35969469480](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35969469480)で、既存の削除履歴を保護した3件の全件適用、pending・drained・updated・deletedの全4段階と各verify、共有KVを含む回収が成功した。監査22行・11操作、run/version/commitとclean checkout、今回runの `passed`・全manifest `dirty=false`、JUnit 678件成功を照合した。`google_sync_baseline_preserved`・`google_sync_full_input`・`google_sync_shared_cleanup` はすべて200だった。prepare_fullの所要時間は監査上67.236秒で、全件phase90秒・HTTP120秒・workflow内MCP180秒の設定で完了した。今回の3件・専用環境を超える任意構成の検証は別項目とする。

### Google同期の予定形式・件数・共有queue再試行

`POST /admin/e2e/google-sync/matrix`、MCPの `prepare_matrix`、手動workflowの `deploy-and-google-matrix-smoke` を使う。既存の2件・3件モードは維持する。専用環境・共有6キー欠損・既存削除履歴の保護・通常書込み/Cron無効・version照合は全件モードと共通。

対象は通常予定2件（うち1件はUTCで日付をまたぐ2時間）、3日間の終日予定1件、日次2回の繰返し予定（展開後2件）の計5件。初期検査、通常予定3件の個別作成、繰返し親の作成とinstance記録を別HTTPへ分割する。Googleが返したinstance IDを、親ID・originalStartTime・run marker・内容・開始終了時刻で照合してから保存し、各回に固有markerを付ける。IDの生成形式は推測しない。繰返しinstanceの識別と個別変更は[Googleの繰返し予定仕様](https://developers.google.com/workspace/calendar/api/guides/recurringevents)に従う。

現行版は後半で通常予定を2件追加し、合計7件（削除済み2件を含む）・28stepを対象にする。所有manifestの `matrix_extended=true` は途中変更を拒否し、step 27まで成功しないとpassedにしない。旧5件・18stepのdirty runは旧完了条件と回収経路を維持する。

| step | 処理と読戻し |
| --- | --- |
| 0〜4 | 初期検査、終日・通常2件・繰返し親と2回分の段階作成 |
| 5〜7 | 上限2件で5件を処理し、共有queueを3件→1件→0件へ消化 |
| 8〜9 | 終日予定を1日移動、繰返しの1回だけを1時間移動し、説明も更新 |
| 10〜11 | 終日予定と繰返しの1回だけを削除し、もう1回を維持 |
| 12 | 通常予定の説明を更新し、所有Discord予定への不正日時PATCHで400・code 50035を確認。Notion部分反映、残件1件、cursor・最終成功時刻の維持を照合 |
| 13 | 次のHTTPで保存済みqueueだけを再試行し、既存Notion/Discord IDを維持して完了 |
| 14 | 残存3件の説明を更新し、所有Discord予定3件すべてで不正日時PATCHへの400・code 50035、Notion部分反映、残件3件、cursor・最終成功時刻の維持を照合 |
| 15〜17 | 上限1件で共有queueだけを別HTTPから処理し、残件3→2→1→0、未回復Discordの旧説明、回復後の新説明、既存ID維持を照合 |
| 18〜19 | 通常予定を1件ずつ追加し、所有記録とGoogle読戻しを確認 |
| 20〜23 | cursorを参照せず、Googleの通常ページ送りを `maxResults=2` で実行。削除済み2件を含む所有7件と4ページ以上を必須とし、上限2件でqueueを5→3→1→0へ消化 |
| 24〜25 | 追加予定の説明を変更し、所有確認後にNotion更新の失敗を固定注入。Notionは旧説明・Discordは新説明、残件1件・cursor保護を確認し、次のHTTPでqueueだけを再試行 |
| 26〜27 | もう1件の追加予定を削除し、所有確認後にDiscord削除の失敗を固定注入。Notionはarchive済み・Discordは残存、対応表・残件1件・cursor保護を確認し、次のHTTPで削除を完了 |

各stepを別HTTPでverifyし、Googleの内容・開始終了時刻、Notionの日時と説明、Discordの開始終了時刻・説明・削除、対応表とqueueを照合する。終日は既存の通常変換（開始日09:00 JST、exclusive終了日01:00 JST）を維持する。step 6・7・13・15〜17・21〜23・25・27では、全取得入力の所有確認後、通常適用には保存済みqueueだけを渡す。履歴保護の例外以外の所有外入力は拒否する。

小さいページサイズはE2Eから渡すfetch callbackだけで設定し、通常運用の `maxResults=2500` を維持する。[events.list仕様](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)の `nextPageToken` に従って実APIを読み、ページ内の欠落・重複や所有外入力、想定したページ数の不足は適用前に拒否する。Notion更新とDiscord削除の固定注入では対象操作を実行せず失敗値を返し、通常適用のエラー・queue保存分岐を通す。サービス障害やHTTP 5xxを観測した証拠とは扱わず、その後の再試行と他の適用・読戻し・回収には実APIを使う。callbackを渡さない通常処理の動作は維持する。

28step版は[実行36003358730](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36003358730)（commit `e8e7e9d3208fc010e7089281359c2c37126dc981`）で実サービス検証も成功した。所有7件のページ送り、queueの5→3→1→0、Notion更新／Discord削除の固定失敗後の部分反映・cursor保護・次HTTPでの回復・既存ID維持、全28段階と各verify、全所有資源と共有KVの回収を確認した。監査118行・59操作、run/version/commit一致、passed・全manifest dirty=false、JUnit 754件成功をartifactで照合した。通信失敗・ロック解放失敗は再発せず、再試行・診断の実障害による発動は未確認である。

初回の[実行36001946180](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36001946180)はstep 6のadvance後、verifyのfetchが例外となり `worker_request_failed`・status 0で停止した。回収200・`failed_clean`・全manifest `dirty=false`、監査34行・17操作とversion一致を照合済み。7件への拡張段階には未到達。HTTP応答未取得の通信例外であり、詳細原因は旧記録から特定できない。

Google verifyは同runの `worker_request_failed`（status 0）と `worker_response_read_failed`（status 200）だけ、通信失敗を各段階最大3回まで試行する。間隔3秒、状態反映待ちを含む合計25回の上限も維持する。prepare／advanceは応答を失っても再送しない。通信例外の `transport_diagnostic` は `phase`（fetch／body）、許可された `name` と `code` だけを監査・成果物へ保存し、未知の型名・コードは `other` にする。これらは次回の切り分け情報であり、今回の障害原因の推定値ではない。

回収は所有する繰返し親・各回・対応先を区別する。親のrecurrence・各回の所属とmarkerを再確認してから親を削除し、通常予定とNotion/Discord、共有KVも回収する。未知の親・回・変更された繰返し規則ではdirtyを維持する。親作成後の応答喪失やinstanceのmarker更新途中でも、記録済み親と元の開始時刻から所有範囲を確認する。32 KiB manifestの境界を含め、既存削除履歴100件での全段階をローカル検証する。

[test_e2e_google_matrix.py](../tests/test_e2e_google_matrix.py) は実DOロジックと代替APIによる検証である。実サービスでは2026-09-24の[実行35977892750](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35977892750)（commit `1ca952b9a5297e4dcbe5de837ac0669d3af66a20`）で、全14段階と各verify、API拒否後の共有queue再試行、繰返し親・共有KVを含む回収が成功した。監査62行・31操作、run/version/commit一致、今回runの `passed`・全manifest `dirty=false` をartifactで照合した。今回の14stepは上記の有限ケースを対象とし、無制限の件数・繰返し規則・サービス停止や回線断の観測を証明しない。400応答は入力検証によるAPI拒否であり、サービス障害ではない。

18stepへの拡張版は[実行35982356318](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35982356318)で実サービス検証も成功した。全18段階と各verify、残存3件それぞれの400拒否とcursor保護、別HTTPでの上限1件のqueue再試行、既存ID維持、繰返し親・各予定・Notion page・共有KV回収を確認した。監査78行・39操作、run/version/commitとclean checkout、passed・全manifest dirty=false、JUnit 719件成功をartifactで照合した。400以外の応答を期待する拒否の証拠にせず、失敗して回収する。通常同期の件数1・2・5・17と処理上限1・2・5の12組は、[test_google_sync_failures.py](../tests/test_google_sync_failures.py)で残件順序、対応ID数、重複作成なしをローカル検証した。外部APIの実障害、任意構成・件数の保証とは区別する。

### 分割後の状態障害E2Eの実行結果

2026-09-14、fork作業ブランチ `feature/sync-fault-request-split` の `c1740e2f0a5d11dedefe4c06df24f318110ef1f2` を使い、[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)を実行した。Local validation成功とEnvironment承認後、専用Workerを1回deployした。run ID `E2E-20260914T120901Z-7c8d0e77`、Worker version tag、deployと最終version fingerprint、対象commit、実行checkoutのclean状態を照合した。

prepare 1回・advance 7回・verify 1回で、固定KV障害7ケースとTTLケース、別HTTP読戻しがすべて200となった。ケース要求の最大時間は15.191秒、TTLケースは14.051秒、verifyは13.133秒だった。読戻しの再試行は発生していない。run内と `always()` のcleanupが200、`outcome=passed`、全資源 `dirty=false` を確認した。artifact監査24行・完了12操作とmanifest、JUnit 570件・失敗0・エラー0・skip 0を独立照合した。

古い値・保存失敗は固定注入、外部同期は代替runnerである。実KVの伝播遅延や実サービス障害、重複反映の解消を証明しない。TTLケースは1 HTTP内の手動同期共通処理であり、実Cron・別Workerリクエスト間の競合は対象外。実行時点で修正版は未マージで、本番デプロイは行っていない。

### Google同期のready段階からの回収

`deploy-and-google-sync-recovery` は、同run・稼働version tag・他資源cleanを確認し、`cleanup` または処理済みの `ready` 段階から所有資源だけを回収する。処理中の `working` は拒否し、制御ロックのTTLと所有確認を維持する。新規fixtureは作成せず、`failed_clean` と全資源cleanを必須にする。実行35980469928でready段階のロック解放失敗を観測したため拡張した。

回収実行35981499346で、ready段階からの所有資源回収と `failed_clean`・全manifest `dirty=false` を確認した。その後の[18step再実行35982356318](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35982356318)は全段階と回収に成功した。最初の `google_sync_release_failed` の根本原因は未確定であり、再実行成功を原因修正の証拠とは扱わない。

### Google制御ロックの解放診断

`google_sync_release_failed` のHTTP 409応答には `release_diagnostic` を付け、MCPの監査JSONLとrun manifestの `operations` に引き継ぐ。`step` は `release_rpc`（解放呼出し）、`status_rpc`（状態照会）、`release_response`（解放応答）、`status_response`（照会応答）、`lock_response`（lock形式）、`owner_check`（自分のロックが残留）の固定分類。`exception` は `none`・`timeout`・`type_error`・`runtime_error`・`js_exception`・`other` だけを残す。

`release_ok`・`status_ok` は取得した応答のok判定、`owner_matches` は照会したownerと呼出し元の一致判定で、取得・判定できなかった値は `null` とする。`false` と未確認を区別し、例外本文・任意の例外型名・owner値・トークンを保存しない。解放後の照会順序、TTL、失敗時のdirty維持は従来どおりで、自動再試行や強制解放は追加しない。

ローカルではRPC例外・不正応答・自分／他owner・ロック消失を検証し、step 11の固定障害でHTTP 409／cleanupのbusy／TTL経過後の回収を確認する。MCPの応答→JSONL書込み・読戻し→成果物の経路も検証する。固定障害は過去の実障害原因を証明せず、実環境での再発時に切り分けるための診断である。

[実行35994329876](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35994329876)で診断版 `538008a2cb40675e81e8476ca9ee7316b8e285cd` を専用Workerへ反映し、18段階と各verify、所有資源・共有KVの回収が成功した。監査78行・39操作、run／version／commit一致、`passed`・全manifest `dirty=false`、JUnit 739件成功を成果物で照合した。解放失敗は再発しておらず、実障害での診断出力や根本原因の確認は含まない。

## 全体同期の往復・部分失敗E2E

`deploy-and-all-sync-smoke` は、既存の `google_sync` manifestと回収経路を使う9段階のシナリオである。`POST /admin/e2e/google-sync/all`（MCPの `scenario=google_sync, sync_phase=prepare_all`）で開始し、既存の `advance`・`verify`・`cleanup` へ接続する。[実行36007253095](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36007253095)で全9段階と各verify、監査42行・21操作、全所有資源とKVの回収、passed・全manifest dirty=false、run/version/commit一致を確認した。

開始前に専用Calendar・Guild・Notion内部DBの空状態と共有Google同期KVの空状態を確認する。Calendarの既存削除履歴だけを不変のfingerprintで保護する。Google予定2件を所有し、通常dispatchのGoogle取得・適用に続けて通常Discordポーリングを実行する。KVの8キー（Google同期の6キーと `discord:snapshot`・`sync:discord_notion_queue`）はrun・scope別に隔離し、別HTTPでdigestと内容を読み戻す。通常の共有KVを使う全体同期を検証済みとは扱わない。

| step | 操作・確認 |
| --- | --- |
| 0 | 所有Google予定2件を作成し、読戻す |
| 1 | Google→Notion・DiscordとDiscord→Google・Notionを同一dispatchで実行 |
| 2 | 所有Discord予定の本文を変更し、Google・Notionへ反映 |
| 3 | 次の全体同期で往復させ、既存の対応IDと内容を維持 |
| 4 | Google本文変更後、所有Notionページ更新だけ固定失敗にし、Google queue・cursor・最終成功時刻を確認 |
| 5 | 次のHTTPで通常queueから回復し、同じNotion・Discord IDを維持 |
| 6 | Discord本文変更後、所有1件の逆方向適用だけ固定失敗にし、Discord queue・snapshot・最終成功時刻を確認 |
| 7 | 次のHTTPで逆方向を回復し、Google・Notionの本文と対応IDを確認 |
| 8 | 所有KVの時刻でクールダウンを検査し、manual・webhook・cronの各source間の競合拒否、例外後のロック解放を確認 |

通常実装はDiscord由来のGoogle予定をDiscordへ再反映しない。既存のループ抑止仕様を変更せずに試験する。GoogleのPATCHでE2E所有markerを維持する挙動は、[Google拡張プロパティの仕様](https://developers.google.com/workspace/calendar/api/guides/extended-properties)に基づく。

各stepの適用後に別HTTPのverifyを必須とし、MCP・workflowはstep証跡、固定失敗の到達、クールダウン、排他、run・version一致を照合する。他シナリオのdirty manifestがある場合は開始を拒否し、全体同期中の別シナリオ開始もHTTPとDOの両方で拒否する。run・対象・モードの変更、所有外入力、対応IDの変更を拒否する。全段階verify後の回収だけ `passed`、途中回収は `failed_clean` とし、回収失敗時はdirtyと所有記録を残す。回収専用モードは既存の `deploy-and-google-sync-recovery` を使える。

ローカル試験は外部HTTPを代替する。実環境でもstep 4・6は固定失敗の注入、step 8は1 HTTP内の共通dispatch呼出しであり、実サービス障害・実Webhook受信・Cloudflareの実Cron起動・別Workerリクエスト間の競合を証明しない。作成通知は無効化する。

## 通常HTTP入口と共有KVによる全体同期

`deploy-and-all-http-smoke` はE2E専用Workerの `POST /sync/all` を使う4段階のシナリオである。`POST /admin/e2e/google-sync/http` で所有Google予定2件を準備し、各段階を別HTTPでverifyする。通常入口では元のRequestを `entry.Default.fetch` へ渡し、通常のStateStore・dispatch・Google取得／適用・Discordポーリングを使う。同期runnerや取得結果の差し替え、固定障害注入は行わない。

1. step 0: Calendar・Guild・Notion内部DBと共有KVの空状態を確認し、所有予定2件を作成する。
2. step 1: `/sync/all` でGoogle→Notion・DiscordとDiscord→Google・Notionを実行する。
3. step 2: 所有Discord予定の本文を変更後、次の `/sync/all` でGoogle・Notionへ反映する。
4. step 3: 再度 `/sync/all` を実行し、内容・既存対応ID・空queue・snapshot・成功結果を読み戻す。

共有KVは通常名の7キー（cursor、2対応表、Google queue、結果、Discord snapshot、Discord queue）に保存する。`sync:last_epoch` は通常構成どおりglobal DOへ保存し、別HTTPで同値を確認する。KVアダプターはキーを改名せず、所有run・内容digestの確認と書込み前の回収記録だけを行う。読取り値をDOやメモリで代替しない。KVの `sync:last_epoch` を含む8キーすべてが開始時に空であることを要求する。

`E2E_ALL_HTTP_ENABLED=true`、認証、run・稼働version一致、所有manifestの準備・verifyが揃う場合だけ通常入口を開く。adminのadvance経由では進めない。各dispatch前に全Calendar・Guild・内部DBの所有範囲を確認し、既存の削除履歴は事前fingerprintと一致する場合だけ許容する。削除履歴も通常同期へ渡し、Googleの処理上限は履歴上限100件と所有2件の計102件とする。実行中に外部から別データを書き込む環境の保証ではない。

回収は所有予定・ページ・共有KVの記録済みdigestだけを対象にする。DOの最終成功時刻は通常同期の実行履歴として維持する。Google認証はリクエスト内、作成通知と実Cronは無効。途中失敗を回収成功でpassedに変えず、4段階verify後だけpassedとする。本番環境、通常通知、実Cron、実Webhook、障害回復はこのモードの対象外である。

初回[実行36010039102](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36010039102)は最初の通常HTTPで `google_sync_state_invalid` となり、所有資源・共有KVを回収して `failed_clean` になった。通常処理がDiscord由来の削除履歴を対応表へ残すケースで同じ拒否をローカル再現した。E2Eの許容範囲へ開始前に確認した履歴の対応ID fingerprintを加え、未知・改変された対応は引き続き拒否する。通常同期の挙動と取得一覧は変更しない。状態形式の拒否では、値を含めずmap／queue／snapshotの固定分類を返す。

再実行[36010723441](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36010723441)（commit `02bc807275c03cf9dddfa3aa7367dd5ae91100cb`、run `E2E-20260924T141102Z-1f6eb5a4`）は実サービス検証に成功した。監査22行・11操作から通常 `/sync/all` 3回と各verifyを確認し、4段階の完了、対応IDと本文、共有queue・snapshot・結果、DO成功時刻を照合した。既知削除履歴の対応を含む通常処理が通り、所有資源と共有KVを回収した。成果物からrun・稼働version・commit・clean checkoutの一致、`passed`、全manifest `dirty=false`、JUnit 790件成功を独立確認した。初回失敗の記録は維持し、この成功へ置き換えない。

## 通常watchと共有状態を使う実Webhook同期

`deploy-and-watch-shared-smoke` は専用環境の所有予定2件を使う。`prepare_webhook` で通常の `ensure_watch_active` を通常HTTP入口から実行し、登録、有効時の無更新、期限しきい値による更新、期限欠損、token変更と復元、停止後の再登録を確認する。期限の経過を待つ試験ではなく、しきい値と所有するwatch状態を設定する試験である。6個のchannel IDはAPI呼出し前に所有記録へ保存し、初回 `sync` のresource IDはwatch応答より先に届いてもDOへ保存する。

各 `webhook_trigger` は所有Google予定のprivate propertyを更新する。Googleから実際に届いた `exists` を認証後にrun所有DOへ保存し、Alarm → 通常Webhook handlerのlease・重複抑止 → 通常同期dispatchへ入り、通常名の共有KV、global DOのロック・成功時刻、通常のGoogle/Notion/Discord処理を使う。Google全件入力は事前に所有予定と既知の削除履歴だけであることを確認する。3回の実通知で全体同期の往復と共有状態の別HTTP読戻しを確認する。各回で受信した同じrequestを内部再送し、KVと成功時刻が変化しないことを確認する。これはGoogle自身による同一通知の再配信を保証しない。

初回同期の前には、所有channelの別通知番号で次の再試行を確認する。

- 同期ロック取得中は503となり、ロック解放後の同じ通知番号で同期成功・成功時刻更新を確認する。
- 固定の無効bearerによるGoogle取得失敗は500となり、有効bearerへ戻した後の同じ通知番号で同期成功・成功時刻更新を確認する。

復旧を `watch_shared_busy_retry_recovered=200` と `watch_shared_failure_retry_recovered=200` に記録する。成功後の同番号通知では共有KVと成功時刻が変化しないことも確認する。異常系は内部生成通知であり、Google自身による同一通知の再配信や自然なAPI障害を観測する試験ではない。実Google通知による正常同期3回には `watch_shared_alarm_<step>=200` も必須とする。


旧channelと所有外channelの拒否はE2E入口の所有権ガードによる。通常Workerでの旧channel拒否を証明しない。token変更中の初回通知は通常token検証で拒否され、最終channelの `sync` を別HTTPで確認する。実Cron、本番Worker、自然な期限切れ、Googleの再送間隔は対象外。

回収はwatch停止を先に行い、所有DO通知キューとAlarm、所有通知のdedupeと観測記録、Google予定・Discord予定・Notionページ、watchを含む共有KVの順で確認する。共有値が所有記録と一致しない場合は削除せず、`dirty=true` を維持する。global DOの成功時刻は実行履歴として残す。

2026-09-25の[実行記録](E2E-WATCH-SHARED-20260925.md)に修正前の失敗と修正後の結果を記録する。修正後の実行36025938367では、実通知3回のAlarm・共有状態同期・往復、固定失敗後の同番号再試行、全所有資源と通知キュー・Alarmの回収が成功した。最終状態読取りの通信失敗でworkflow自体は失敗したため、読取りだけの限定再試行を追加した再実行36027225893はworkflow全体が成功した。監査104行・52操作、run/version/commit一致、`passed`・全manifest `dirty=false`、JUnit816件成功を照合済み。

## 通常Q&Aジョブの共有状態E2E

`deploy-and-qa-normal-smoke` は既存の専用Worker・Q&A DB・Discordチャンネルを使う。
開始時にDBが空であり、通常名の `qa_cache` と `result:job_qa_check` が未設定であることを要求する。
3件（回答済み・番号41が1件、未回答・番号未設定が2件）を作成し、次の5段階と各段階の別HTTP読戻しを実行する。

1. `prepare`: 対象と空状態の確認、run marker付き3件の作成。
2. `first`: 通常 `Application.fetch` の `/jobs/qa-check` 分岐を呼び、全件取得・42/43の採番・初回通知抑止・共有cacheを確認。
3. `update`: 65秒待って3件の質問を更新し、実更新時刻の変化を確認。cacheの時刻は加工しない。
4. `notify`: 同じ通常ハンドラを実行し、未回答2件の通知内容と回答済み1件の抑止、共有cache・結果を読み戻す。
5. `duplicate`: 再実行後も同じ2通知だけが存在することを確認。

MCPの `trigger_job(job="qa_normal_<phase>")` は認証・run/version照合・globalロック付きの固定管理routeを呼ぶ。
通常ハンドラへ渡すDB一覧は加工しない。KVアダプターは通常名の2キーを実KVへ通し、DOに所有runと書込み予定digestを記録する。
読戻しだけを待機再試行し、通知処理は自動再送しない。
終了・途中失敗時は既存の `cleanup_run(service="qa_notification")` で所有page・message・KVだけを回収する。
全段階の検証と回収が成功した場合だけ `outcome=passed`・`dirty=false` を保存する。

これは3件の通常ジョブ処理の検証である。実Cron配信、100件超のページ送り、通知失敗後の再試行、一度限りの配信保証は含まない。
外部から通常URLへ直接到達する検証ではなく、保護された管理routeから通常HTTPハンドラへ委譲する。

2026-09-25（JST）の[実行36030243998](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36030243998)で成功した。対象commit `ee87f2591c9848f4ef5ac529539b3b3f661121c3`、run `E2E-20260924T165255Z-4ee17edc`、Worker version tagとdeploy／最終version fingerprint、clean checkoutを照合した。全5段階と各verify、通常ハンドラ3回、Notion3ページのarchive・Discord2通知の削除・共有KV2キーの回収が成功した。監査26行・13操作はすべて成功し、`outcome=passed`・全manifest `dirty=false`、JUnit825件成功を独立確認した。ローカルMCP・workflowテスト297件、Ruff・Pyright・設定検査・E2E dry-runも成功した。マスク済み成果物と独立照合結果は `test-results/qa-normal-36030243998/` に保存した。


## 通常リマインドの全件取得と共有KV

`deploy-and-reminder-normal-smoke` は空の専用Guildへrun marker付き予定4件を作成する。
実時刻から24時間8分後・10分後の2件を通知対象、23時間後・25時間後の2件を範囲外とする。
`prepare → notify → duplicate` の各段階を別HTTPで実行し、各段階後に別HTTPの `verify` を行う。
通常 `Application.fetch` の `/jobs/reminder` 分岐は予定一覧を加工せず全件取得し、
通常 `StateStore` が `reminder_cache` と `result:job_reminder` を専用環境の実KVへ保存する。
KVアダプターはキー・run所有権・値のdigestを検証し、DOには回収用の所有記録を保存する。

本文・対象roleのみのmention・対象2件だけのcache・同一message IDの維持・cache書込み1回を確認する。
2回目も通知時刻内であることを確保するため、開始から6分を超えたジョブ実行は拒否する。
初期状態が空でない場合、所有外予定、別run、異なるWorker revision、順序外の実行も拒否する。
失敗時も作成応答を失った予定をpayloadとmarkerで再発見し、所有予定・通知・共有KVだけを回収する。
既存の `deploy-and-reminder-smoke` は1件と実行内cacheの試験として残す。
このモードは実Cron配信、API実障害、通知失敗後の通常ジョブ再試行、KVの全リージョン一貫性を証明しない。

通常リマインドはDiscord一覧取得のHTTP失敗・不正形式を空一覧として成功扱いせず、失敗statusを返す。
通常DiscordジョブのHTTP呼出しには[公式形式のUser-Agent](https://docs.discord.com/developers/reference#user-agent)を付与する。
初回36032380828は通知後verifyで停止し、所有資源・共有KVの回収と `failed_clean`・全manifest `dirty=false` を確認した。
初回は一覧のHTTP statusを記録していないため、User-Agent不足との因果関係は確定していない。

2回目36033000148は通常一覧取得のHTTP 429を記録し、通知前に停止して全資源を回収した。
通常ジョブのDiscord GETに限り、`retry_after` が有限かつ0〜10秒の場合に最大4回まで試行する。
POST・不正待機値・上限超過は再試行しない。継続する429も成功扱いにしない。

[実行36033540656](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36033540656)（commit `cc97b8b`）で、専用Guildの予定4件を通常HTTPハンドラから全件取得し、対象2件の通知・範囲外2件の抑止・共有cache・別HTTPでの重複抑止を確認した。全3段階と各verify、所有予定・通知・共有KVの回収が成功した。監査18行・9操作、run/version/commit一致、`passed`・全manifest `dirty=false`、JUnit 847件成功を独立照合済み。実Cronと通知失敗後の再送は対象外。
証跡は `test-results/reminder-normal-36033540656/evidence/`、独立照合結果は同runディレクトリの `verification.json` に保存した。
