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

`tests/test_discord_sync_lock.py` は手動・Cronと全体同期の共通ロック、競合時の最終結果保護、適用・結果保存の例外とキャンセル後の解放、取得エラー時の停止、明示無効時の互換性を確認する。並行HTTPの検証では、1件が適用中の間にもう1件が409で拒否されることを確認する。

これらはローカル検証であり、実KVの伝播遅延、ロックTTL超過、実Cron、通常Guild全件への実サービス適用は未検証である。run所有checkpointを使う専用Discord差分E2Eの成功も、通常の共有KVの実動作を証明しない。

通常ポーリングへの接続では、`discord_batch` の初回・advanceで `run_discord_notion_poll_sync` を呼ぶ。同じ一覧API取得を通し、状態読込み前にDOで所有する2件を選別する。所有ID・run marker・guild・初期内容が一致し、重複と欠落がないことを検証する。一覧順が変わってもDOに記録した順序で処理し、他のイベントは差分処理・KV・Notion反映へ渡さない。`batch_first_poll` / `batch_remaining_poll` を成功証跡へ記録する。通常の呼出しは選別・適用runnerを指定せず、従来どおり全件を扱う。

追加のローカルテストは、有効な他イベントの混在と逆順、初回・advanceそれぞれの一覧欠落・重複・内容変更・不正要素・HTTP失敗を検証する。拒否時にはNotionとKVへ書き込まず、dirtyを保持して回収する。Google同期・通知・手動/Cronの通常HTTP入口はこのシナリオへ未接続で、上記実行34614558706は接続前の差分処理直接呼出しの証拠である。

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
| `deploy-and-discord-kv-smoke` | 所有Discord event 1件を通常差分処理でNotionとKVへ反映し、別HTTPで読み直して外部資源と固定2キーを回収する |
| `deploy-and-discord-batch-smoke` | 所有Discord event 2件を上限1件ずつNotionへ反映し、KVの残件を別HTTPで読み直して消化・回収する |
| `deploy-and-google-discord-smoke` | 専用 Worker を deploy し、Google event を既存の適用処理で Discord Scheduled Event へ反映して検証後、両資源を cleanup する |
| `deploy-and-google-notion-smoke` | 専用 Worker を deploy し、Google event を既存の適用処理で Notion 内部 DB へ反映して検証後、両資源を cleanup する |
| `deploy-and-qa-notification-smoke` | 専用 Worker を deploy し、所有Q&A pageの初回抑止と更新通知を検証後、Notion pageとDiscord messageをcleanupする |
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

`tests/test_e2e_discord_batch_probe.py` は固定2件の上限・残件処理、別HTTPの状態復元、古いKVの2回目読込による処理済みイベントへの再適用拒否、保存・削除・部分回収の失敗、所有権変更、再検証失敗後の成功判定取消しを代替APIで確認する。通常ポーリング、Google同期、作成通知、TTL超過、実サービスでの保存失敗注入は含まない。2026-09-12（JST）の[実行34614558706](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34614558706)でprepare→残件確認→advance→最終確認→cleanupが成功した。各verifyは1回、prepare / advanceも各1回である。監査とmanifestの7操作、初回上限・残件読戻し・残件適用・最終読戻し・KV回収の各200、Discord削除204とNotion archive 200を各2件、version・run一致、outcome=passed、全資源dirty=falseを独立照合した。KV遅延による待機は発生していない。cleanupは外部API応答とKV deleteの完了を確認し、全拠点の削除反映は保証しない。

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
