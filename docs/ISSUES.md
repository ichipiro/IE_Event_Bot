# 課題

## 運用方法

この文書には、現行コード、設定、文書から確認できた課題と解決結果を記録する。GitHub、Cloudflare、Discord、Google、Notion の現在状態が必要な項目は、確認日と検証方法を併記する。

状態は `未対応`、`対応中`、`確認待ち`、`完了` のいずれかを使う。

## 課題一覧

### ローカル単体テスト基盤

- 状態: 完了
- 根拠: `tests/` に外部通信を遮断するテスト基盤と、認可、クールダウン、ロック、Webhook 重複、キュー繰り越しの単体テストを追加した。
- 対応: CI で `pytest -q` を常時実行し、テストが収集されない状態を成功扱いにしない。
- 継続方針: watch、認証ソース、定期ジョブ、外部 API 応答別のテストは、各機能変更時に拡張する。

### `INTERNAL_API_TOKEN` 未設定時に同期・管理・ジョブ API が公開される

- 状態: 完了
- 根拠: `workers/src/entry.py` の `_authorized()` は、`INTERNAL_API_TOKEN` が未設定、空、欠落、不一致のいずれでも認可に失敗する。
- 対応: 同期、管理、ジョブ API は処理開始前に `401` を返す fail-closed とし、未設定時の回帰テストを追加した。
- 実環境境界: Cloudflare 上の Secret 登録状態はローカルでは未確認であり、デプロイ時に別途確認する。

### Google Webhook の送信元認証が限定的

- 状態: 完了
- 根拠: Google watch 登録時に `GCAL_WEBHOOK_TOKEN` を channel token として設定し、受信時に `X-Goog-Channel-Token` と照合する。
- 対応: Secret 未設定時は `503`、ヘッダー欠落・不一致時は `401` を返し、同期と重複状態更新の前に拒否する。旧 watch または token 変更時は SHA-256 fingerprint の不一致から再登録する。
- 多層防御: token 検証に加えて、重複抑止、クールダウン、Durable Object ロックを維持する。Cloudflare WAF とレート制限は実環境で必要性を判断する。
- 実環境確認: 2026-09-02の[初回実配信workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33616522253)で共通channel token付きの短命watch作成、Googleからの初回`sync`通知、watch停止を確認した。続く[変更起因workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33623500404)で、所有event更新後の実`exists`通知、最初の通知だけのclaim、共通dispatch、所有event 1件のNotion適用、watchと全所有資源のcleanupを確認した。通常Workerの既存watch再登録・更新、共有cursorと全Calendarの全件適用は含まれない。

### ローカル詳細文書の追跡方針が分かれている

- 状態: 完了
- 根拠: `docs/Event_Bot仕様書.md`、`docs/KV.md`、`docs/Operations.md`、`docs/do-kv-design.md` は存在するが `.gitignore` 対象である。
- 決定: 4文書は追跡対象外のローカル補助として維持する。削除、追跡追加、本文変更は行わない。
- 対応: クリーンなチェックアウトで必要な現行要件は追跡対象の標準文書へ記載し、ローカル補助だけを正本にしない。
- 検証: `AGENTS.md` では4文書をリンクではなくパス表記とし、追跡対象 Markdown の相対リンクがクリーンなチェックアウトで解決する状態を保つ。

### Wrangler のローカル版が固定されていない

- 状態: 完了
- 根拠: `package.json` で Wrangler `4.127.1` を完全固定し、`package-lock.json` で解決済み依存と整合性を固定した。
- 対応: `npm ci` でローカル版を導入し、`npm run wrangler -- ...` で実行する。グローバル Wrangler は前提にしない。
- 検証: 固定版の `--version`、依存監査、`workers/wrangler.jsonc` を指定した `deploy --dry-run` が成功した。実デプロイは行っていない。

## 外部状態を伴う課題

### 通常Discord同期の作成通知が繰越・失敗後に消える

- 状態: 確認待ち
- 原因: snapshotを進めた後の繰越イベントが新規判定から外れ、通知失敗も再試行queueへ保存されていなかった。
- 対応: queueへ通知先と投稿済みmessage IDを保持する。通知だけの再試行では同期済みイベントを再適用せず、リアクション失敗では再投稿しない。
- 検証: 修正前の再現テスト4件が失敗し、修正後の通知テスト22件が成功した。`discord_batch_notification` で所有メッセージの記録・回収、別HTTPでの再試行、専用workflowへの接続を追加し、ローカル検証した。実サービス通知・回収は未検証で、投稿応答の喪失やKVの不整合を含めた一度だけの配信は保証しない。

残作業と完了条件は [E2E-PLAN.md](E2E-PLAN.md) で追跡する。項目10の5件は2026-09-11のユーザー指定により追加対象外とし、過去の「未確認」記述は追加作業の指示として扱わない。

### 通常Discord同期の共有状態と排他

- 保証範囲の確定: [保証範囲表](TESTING.md#状態障害の保証範囲表)と追加8ケースで、両キーの古い読取り・旧形式による削除待ち喪失、再適用・重複通知、owner確認後の期限切れによる新結果上書きをローカル再現した。項目3の境界整理は完了したが、これらの制限を解消したものではない。
- 状態障害のローカル検証: `sync_faults` に古いsnapshot / queue 3ケースと、外部適用の代替runner成功後のKV保存前後失敗4ケースを追加した。古い空queueと最新snapshotにより、未処理イベント1件が別イベントの再試行queueで上書きされる条件を再現した。対策としてsnapshotの各指紋に `_pending_sync` を付け、queueとの和集合から残件を復元するよう修正した。作成・更新・削除・件数上限残件・通知待ちの再現5テストが修正前に失敗し、修正後は成功した。専用シナリオも残件回復を必須とした。実行34839754885ではKV障害7ケースのprepare処理が完了したが、TTLと別HTTP読戻し前に停止したため、この実行を検証完了とは扱わない。分割後は[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)で8ケースと別HTTP読戻し・回収が成功した。両キーとも古い場合や、残件情報のない旧形式snapshotと古いqueueの組合せからの復元は保証しない。保存失敗・古い値による再適用も確認しており、一度限りの反映は保証しない。
- 状態障害E2Eの時間上限: [実行34839754885](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34839754885)はprepare約51.4秒で409となり、50秒のphase上限への到達が疑われる。回収200・`failed_clean`・全資源 `dirty=false` を確認済み。対策として1ケースずつ別HTTPに分割し、途中着手記録・固定順・書込み再送拒否・明示的なtimeoutエラーを追加した。分割後は[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)で各ケース最大15.191秒、TTL 14.051秒、verify 13.133秒で成功した。`passed`・全資源cleanを確認済み。
- TTLへの追加対応: 期限・所有者を失った旧実行の結果保存を拒否するよう修正した。手動・Cron分岐・全体同期とGoogle適用後のcursor保護をローカル確認した。DO確認とKV書込み間の競合、同期本体内のqueue保存、実行済み外部書込みは保護範囲外である。10秒TTLと新旧実行を使う専用E2Eは[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)で実KV・DOでも成功した。

- 共通ロックE2E確認済み: `sync_lock` は手動・Cron分岐・全体同期の共通ロック処理を通し、1 HTTP内の競合拒否、成功・固定例外後の解放、所有結果KVの別HTTP読戻しと回収を検証する。同期本体は検査用runnerへ差し替える。実KV・DOでも[実行34834547224](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34834547224)で6 round、結果読戻し、KV回収、version一致、`passed`・全資源 `dirty=false` を確認した。実Cron配信・別Workerリクエスト間の競合・TTL超過は未検証である。

- 実環境で確認済み: 通常StateStoreをrun・scope別の固定2キーへ隔離し、DO所有manifestに基づく保存・別HTTP読戻し・回収を実装した。[実行34604249166](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34604249166)で各HTTP 200、version一致、回収後のoutcome=passedと全資源dirty=falseを確認した。外部fixture 1組との接続も[実行34605517604](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34605517604)で通常差分処理・KV読戻し・外部資源とKVの回収まで確認した。固定2件・上限1件・KV残件の別HTTP消化と回収も[実行34614558706](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34614558706)で成功した。通常ポーリングの一覧取得から所有2件のNotion反映・KV残件処理へ接続し、一覧の欠落・重複・変更・異常応答の拒否をローカル確認した。この経路も[実行34615847619](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34615847619)で初回・残件の一覧取得、Notion反映、KV読戻し、全資源回収まで成功した。Google反映も別の `discord_batch_google` として[実行34619150601](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34619150601)で対応ID・KV残件・3サービス各2件とKVの回収まで成功した。通知接続も `discord_batch_notification` として[実行34831533775](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34831533775)で通知2件、同じmessageへのリアクション再試行、別HTTP残件処理、全所有資源とKVの回収まで成功した。初回リアクション失敗はAPI呼出し前の固定注入であり、実サービスの障害は発生させていない。共通ロック競合と固定注入のKV保存失敗・古い値・TTL超過は専用環境で確認済み。実KV伝播遅延・実Cron・別Workerリクエスト間の競合は残作業である。
- ローカル修正済み: queue保存失敗時にsnapshotだけ進む問題をqueue先行保存へ変更し、手動・Cronの単独同期を全体同期と同じDOロックで保護した。作成・更新・削除の再試行、上限残件、並行HTTP、例外・キャンセル後の解放を代替KV・DO・外部APIで確認した。
- 未確認: 実KVの伝播遅延、通常Guild全件の適用と実Cron、TTL超過中の全書込みの排他。外部成功後の状態保存失敗では再実行され得る。複数キーの原子的更新と一度限りの適用は保証しない。

Fork、Upstream、GitHub Actions、Release Please、branch protection の確認結果は `docs/fork-upstream-workflow.md` に記録されている。これらは変化し得るため、作業前に GitHub 上の現在状態を再確認する。

### サービス間同期・Webhook・定期ジョブの自己cleanup型 E2E

- 状態: 対応中
- 対応済み範囲: Google→Notion は、専用 Google event と Notion page を同じ強整合 manifest で所有し、既存のアプリケーション適用処理を通した検証と自己 cleanup を行う専用 scenario を実装した。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33579456642)でdeploy、実サービス適用、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: Google→Discord は、専用 Google event と Discord Scheduled Event を同じ強整合 manifest で所有し、既存の `_sync_to_discord` を通した検証と自己 cleanup を行う専用 scenario を実装した。通常 KV の同期対応表と queue、Notion は変更対象にしない。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33582230579)でdeploy、実サービス適用、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: Discord→Notion は、専用 Discord Scheduled Event と Notion page を同じ強整合 manifest で所有し、既存の `_sync_discord_event_upsert` を通した検証と自己 cleanup を行う専用 scenario を実装した。Google、外部 Notion DB、通常の Discord snapshot / queue、作成通知は変更対象にしない。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33586744127)でdeploy、実サービス適用、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: Discord→Google は、専用 Discord Scheduled Event と Google event を同じ強整合 manifest で所有し、既存の `_sync_discord_event_upsert` を通した検証と自己 cleanup を行う専用 scenario を実装した。Notion、通常の Discord snapshot / queue、作成通知は変更対象にしない。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33591103445)でdeploy、実サービス適用、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: QA通知は、専用Notion Q&A pageとDiscord messageを同じ強整合manifestで所有し、通常ジョブと共通の初回抑止・更新通知処理を1件だけへ適用して自己cleanupする専用scenarioを実装した。共有 `qa_cache`、Q&A DB全件取得、質問番号補完は対象外である。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33593477413)でdeploy、実サービス通知、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: 前日リマインドは、専用 Discord Scheduled Event と message を同じ強整合 manifest で所有し、通常ジョブと共通の通知ウィンドウ判定と重複抑止を1件だけへ適用して自己 cleanup する専用 scenario を実装した。共有 `reminder_cache`、Guild の通常 event 一覧処理、実 Cron は対象外である。2026-09-02の[専用 E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33599347577)で deploy、実サービス通知、重複抑止、両資源 cleanup、マスク済み artifact の独立確認まで成功した。
- 対応済み範囲: Notion期限cleanupは、専用内部 DB の期限到来・将来日時 page を同じ強整合 manifest で所有し、通常ジョブと共通の期限判定を2件だけへ適用する専用 scenario を実装した。共有 `cleanup:last_epoch`、内部 DB の通常全件取得、実 Cron は対象外である。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33603941069)でdeploy、期限到来pageだけのarchive、将来日時pageの維持、interval guard、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: Webhook simulationは、専用Google eventとNotion pageを同じ強整合manifestで所有し、通常同期と共通のGoogle差分取得とdispatchを通した後、取得結果のevent IDとrun markerが一致する1件だけを適用する専用scenarioを実装した。同期cursor、最終実行時刻、最終結果、Google認証cache、対応表、queueは実行内へ閉じ込める。実Webhook配信、watch、token、重複抑止、実Cronは対象外である。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33609185862)でdeploy、Google差分取得、所有eventだけのNotion適用、実行内状態の分離、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: Webhook ingress simulationは、通常Workerと共通のhandlerでchannel token不一致の事前拒否、正しいtokenによる1回目のdispatch、同じchannel IDとmessage numberによる2回目の重複抑止を確認する。重複状態はrun ID付きでDurable Objectに所有し、manifest内のfingerprint一致後だけ削除する。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33613460405)でrun ID付きWorker versionのread-back、token拒否、初回dispatch、重複抑止、Google / Notion適用、全資源cleanup、`dirty=false`、マスク済みartifactの独立確認まで成功した。この採用runは内部requestによるsimulationであり、Googleからの実配信とwatch channel作成は次項の別scenarioで確認した。
- 対応済み範囲: Google Webhook初回実配信は、run所有channelを外部request前に強整合manifestへ記録し、共通channel tokenと600秒TTLを付けた短命watchだけを作成する専用scenarioを実装した。Googleから`/gcal/webhook`へ到達した初回`sync`通知をwatch応答との順序にかかわらず所有resourceへ紐付け、直後にwatchを停止する。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33616522253)で対象revisionとWorker version tagの一致、watch作成`200`、初回配信`204`、watch停止`204`、cleanup、`dirty=false`、マスク済みartifactの独立確認まで成功した。通常同期dispatch、共有状態、watch更新、実Cronは対象外である。
- 対応済み範囲: Google変更起因Webhookは、run所有event、短命watch、最初の`exists`通知、Notion page、message重複状態を同じ強整合manifestで所有する専用scenarioを実装した。実callbackを通常Workerと共通のWebhook ingressと同期dispatchへ通し、Google差分結果からevent IDとrun markerが一致する1件だけをNotionへ適用する。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33623500404)で対象revisionとWorker version tagの一致、初回`sync`、event更新、実`exists`配信、所有event限定dispatch、実行内状態の分離、watchと全所有資源のcleanup、`dirty=false`、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: Discord差分モードを追加し、通常同期と同じ一覧取得・差分処理へ所有event 1件だけを渡して新規作成・変更なし・説明更新・キャンセル・削除を検証する。snapshot / queueはrun単位で `discord_delta` manifestへ一括保存し、次の差分処理で復元する。Discord eventとNotion pageも同manifestで所有・cleanupする。2026-09-11に外部APIを代替したローカルテストを実施した。キャンセル後の一覧の両応答形、削除後のarchive読戻し、削除queue再試行、完了eventの保護をローカルで確認した。状態オブジェクトとDOオブジェクトを作り直したqueue再試行、保存revision競合、cleanup時のcheckpoint消去もローカルで確認した。準備完了境界から別HTTPリクエストで続行する経路、続行の単一claim、成功後の再送を実装・ローカル検証した。[実行34581609741](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34581609741)で実サービスのprepare / resume、全6回の差分・checkpoint処理、キャンセル後の一覧消失、削除・archive読戻し、cleanupとdirty=false、version tag一致まで成功した。任意位置からの途中再開、実Worker再起動、キャンセル後に一覧へ残る分岐、共有snapshot / queue、Guild全件適用、実Cronは未確認。
- 追加実装: 更新反映・読戻し後の `delta_updated`（revision 3）からも続行できる `advance` を追加し、workflowを `prepare → advance → resume` にした。オブジェクト再作成、再送時の更新重複防止、古いclaimの拒否、保存前中断・保存失敗のcleanupをローカルで確認した。[実行34588410907](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34588410907)で3リクエストの更新後再開、全6回のcheckpoint、cleanup、dirty=false、version tag一致を実サービスでも確認した。再送・競合・オブジェクト再作成はローカル検証のみで、実Worker再起動は未確認。任意の外部書込み位置からの自動再開は対象外。
- 追加実装: 更新後の `advance` と完了後の `resume` の再送を手動workflowへ追加した。期待status・dirty状態の不一致を失敗にし、再送結果の固定statusを監査とmanifestに残す。[実行34589842665](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34589842665)で更新後・完了後の明示再送、固定応答status、全6回のcheckpoint、cleanup・dirty=falseを確認した。実際の応答喪失・同時実行競合・実Worker再起動は含まない。
- 追加実装: 更新完了後に専用Workerを再deployし、異なるversion IDへ続行する。再deployのcheckpoint照合・version ID headerの拒否・失敗後回収をローカルで確認した。[実行34593390627](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34593390627)で2回のdeployのversion ID相違、後続要求の新version指定、全6回のcheckpoint、cleanup・dirty=falseを確認した。DOプロセスの強制再起動は対象外。
- 追加実装: 同run・同versionのresumeを2要求並行送信し、1件の通常完了と1件の入口同期ロック拒否を必須にする。両要求が終わるまでcleanupしない。HTTP入口ロックとDO claimの競合はローカルで分けて確認した。[実行34594293913](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34594293913)で2要求の並行開始、HTTP 200とロック拒否409、完了再送、全6回のcheckpoint、cleanup・dirty=falseを確認した。DO claimそのものの競合はローカル検証に限る。
- 追加実装: MCPで最初のadvanceのHTTP 200ヘッダー受信後に本文を未読のまま破棄する。更新完了checkpointを別取得し、再deploy後の再送・続行へ進む。注入flagと固定エラーを監査に残し、通信失敗や非200応答を注入成功にしない。[実行34597932061](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34597932061)で本文破棄flag、再deploy後のupdated応答、同時resume、全6回のcheckpoint、cleanup・dirty=falseを確認した。回線断やWorker停止の再現は含まない。
- 対応済み範囲: 通常Google同期は[実行34862331643](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34862331643)で、全ページ取得から所有2件を通常dispatch・適用へ渡し、run別KVのcursor・対応表・queue、Notion・Discord反映、上限繰越・残件消化・更新・削除・全資源回収を確認した。別HTTP読戻し、監査・manifest・version・commitを独立照合し、`passed`・全資源 `dirty=false` だった。通常の共有名前空間と任意予定への適用、外部API障害の途中再開は含まない。
- 部分失敗の修正: Google→Discordの作成・更新失敗でIDを得られない場合に、残件を保存せず成功扱いになる不具合を2ケースで再現・修正した。所有2件E2Eへ固定失敗・部分反映読戻し・次回queue処理を追加してローカル検証した。[実行34866761198](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34866761198)で固定注入後の部分反映、cursor保護、次のHTTPでのqueue回復、全資源回収が成功した。監査30行・15操作、run/version/commit一致、passed・dirty=falseを照合した。Discordの実障害を観測した証拠ではない。削除失敗・Notion ID書戻し失敗・応答喪失後の重複は別の未検証範囲。
- 未対応範囲: 任意構成・件数でのGoogle全件適用（専用3件の全件モードと、終日・通常・繰返し計5件の共有queue再試行は検証済み）、Discordの共有snapshot / queue、全体同期、通常watch更新、実Cron、通常QAジョブの全件取得と共有cache、通常リマインドのGuild全件取得と共有cache、通常Notion cleanupの内部DB全件取得と共有状態。
- 2026-09-24の追加修正: Notion照会・取得・作成・archive・Discord ID書戻し、Discord削除のHTTP失敗とsubrequest上限に伴う残件消失・成功誤判定を19ケースで再現・修正した。所有予定の不正PATCHに対するDiscordの400・code 50035と次のHTTPでの回復を既存E2Eへ接続し、ローカル検証した。実サービス実行、共有状態と全件適用の検証は未完了。
- API拒否E2E初回: [実行35952552380](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35952552380)は4段階成功後の拒否試験で失敗し、回収も所有確認に失敗してdirty=trueとなった。空名が受理された場合をローカルで再現し、記録済みID・Guild・run marker・空名を照合する限定回収と回収専用workflowを追加した。実環境の回収・修正版再実行は未完了。
- 上記の回収結果: [復旧実行35955045460](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35955045460)で空名の所有Discord予定を含む全資源を回収し、`failed_clean`・全資源 `dirty=false` を確認した。元の試験を成功には変更せず、不正日時によるAPI拒否・再試行の再実行を残作業とする。
- 修正版の再実行結果: [実行35959152201](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35959152201)で、不正日時へのDiscord HTTP 400・code 50035、cursor保護、別HTTPでのqueue再試行、全6段階と回収が成功した。`passed`・全資源 `dirty=false` とrun/version/commit・artifactを照合した。共有状態と任意予定の全件適用、サービス障害の観測は引き続き未検証である。
- 全件モードの実装: `prepare_full` と `deploy-and-google-full-smoke` を追加した。空の専用環境に3件を作成し、取得順・全入力を維持して通常適用へ渡し、run prefixのない共有KVを所有記録付きで回収する。複数ページ・繰越・更新・削除・途中失敗回収をローカル検証済み。開始時に記録した不変の削除履歴だけを保護して除外する。専用3件の実サービス検証は完了し、任意イベント構成・件数の確認は未完了。
- 全件E2E初回: [実行35962599536](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35962599536)はdeploy後の `google_sync_shared_not_empty` で停止し、新規fixture作成・全件適用は未実行。cleanupは前回runのclean記録を保護してrun不一致となった。全manifest `dirty=false` と前回記録の保持を確認済み。空のE2E専用共有KVを用意して再実行する必要がある。
- 新規KVでの再実行: [実行35963510311](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35963510311)は共有KVの空状態検査を通過し、`google_sync_calendar_not_empty` で停止した。このエラーだけでは残存予定・削除履歴・取得失敗を識別できない。新規fixtureは未作成、全manifestは前回のclean記録を保持。Calendarの開始条件が未解決。
- Calendar診断: [実行35965137690](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35965137690)はHTTP 200・`calendar_deleted`で成功し、通常予定がなく削除履歴だけ残っていることを確認した。予定・KVへの書込みはなく、全manifestは前回と同一で `dirty=false`。開始時の削除履歴を変更不可のハッシュで記録し、一致する履歴だけを除外する修正を追加した。通常予定・未知の履歴・変更された履歴は拒否する。同じCalendarでの全件適用は下記の実行35969469480で成功した。
- 削除履歴対応後の全件E2E: [実行35968760516](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35968760516)は既存履歴保護・3件作成・初回dispatchまで成功し、50秒の `google_sync_timeout` で停止した。今回runは `failed_clean`、全manifest `dirty=false`、共有KV回収成功。全件phase90秒・HTTP120秒・workflow内MCP180秒に調整した版で再検証する。
- 全件E2E成功: [実行35969469480](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35969469480)で、既存の削除履歴を保護した3件の全件適用、pending・drained・updated・deletedの全4段階と各verify、共有KVを含む回収が成功した。監査22行・11操作、run/version/commit、今回runの `passed`・全manifest `dirty=false` を照合した。任意イベント構成・件数、外部APIの実障害、通常Cronは未検証。
- 追加実装: `prepare_matrix` と `deploy-and-google-matrix-smoke` により、終日・通常・繰返しの5件、上限2件での繰越、個別変更・削除、共有queueのAPI拒否・再試行を14stepへ分割した。日時読戻し・instance所有確認・途中回収をローカル検証済み。[実行35977892750](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35977892750)で全14段階と各verify、共有queue再試行、繰返し親・共有KV回収が成功した。監査62行・31操作、run/version/commit一致、今回runの `passed`・全manifest `dirty=false` を照合済み。
- 追加実装: matrixを3日間の終日・UTC日跨ぎ予定と18stepへ拡張した。残存3件のDiscord API拒否、上限1件の共有queue再試行、途中回収をローカル検証済み。件数1・2・5・17と上限1・2・5の12組も確認した。実サービス実行は未完了。
- 18step初回: [実行35980469928](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35980469928)はstep 11のロック解放で失敗し、直後の回収もbusyで拒否された。step 0〜10はverify成功、manifestはready・dirty=true。readyから所有資源を回収するworkflow修正はローカル検証済み。根本原因、実環境回収、再実行は未完了。
- 上記の回収結果: [実行35981499346](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35981499346)で繰返し親・各予定・Notion page・共有KVを回収し、`failed_clean`・全manifest `dirty=false` を照合した。ロック解放失敗の根本原因は未確定。18stepは再実行待ち。
- 18step再実行成功: [実行35982356318](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35982356318)で全18段階、3件のAPI拒否・共有queueの分割再試行、全資源回収に成功した。監査78行・39操作、run/version/commit、passed・全manifest dirty=falseを照合済み。前回のロック解放失敗は今回再現しなかったが根本原因は未確定。任意件数・構成、実サービス障害の観測は未検証。
- 解放失敗の調査: step 11は12.876秒で失敗し、phase上限90秒・TTL300秒とは一致しない。制御DOのrelease削除前障害は後続busyをローカル再現し、正常release後のstatus障害ではbusyにならなかった。RPC例外・不正応答・所有者異常が同一エラーに集約されるため、実環境の原因確定には追加証拠が必要。失敗・成功時の限定ログを照会する `read-only-google-lock-diagnostics` を追加し、ログ取得は未完了。
- 診断のアクセス拒否: 承認済み[実行35987968532](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35987968532)のartifactは `diagnostic_http_403`。Cloudflareログ照会が拒否され、元の障害のログは取得できていない。使用トークンの `Workers Observability Write` 権限と対象アカウントの許可を確認する必要がある。403だけでは権限不足の詳細までは断定できない。
- 上記の再実行: ユーザー報告では既存トークンへ権限追加済みだが、attempt 2も403だった。APIエラーの数値コード・固定分類と、401/403時のuser/account token verifyを追加した。追加診断の実環境結果は未確認。token値・ID・生のエラー本文はartifactへ保存しない。
- 追加診断結果: [実行35991322986](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35991322986)でログ照会403・code 10000に対し、対象account用token verifyは200・activeを確認した。使用中account token自体は有効だがログ照会は拒否される。現在のObservability／Observability Telemetryの権限名とRead/Editを確認中。元のロック解放失敗の原因は未確定。
- 権限案内の訂正: ユーザー画面には「Workersの可観測性 編集」があり、Telemetryの別項目は存在しない。旧名称に基づく追加案内は撤回した。編集したtokenの所有形態と、診断で使われるaccount tokenとの対応を確認する。権限不足・token不一致のいずれも未確定。
- 変更対象の相違: ユーザーは「マイプロフィール → APIトークン」で権限追加したと回答した。一方、診断はアカウント所有tokenを使用しており、変更対象が異なる。GitHubに登録したaccount token側への設定反映と再診断が必要。ログ照会の成功と元のロック解放失敗の原因確定は未完了。
- ログ照会の解決: account token権限更新後、[診断35991322986のattempt 2](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35991322986/attempts/2)が成功し、失敗123件・成功89件のログを取得した。403は解消済み。取得した分類ログに原例外はなく、コードもrelease／status／応答検査の例外詳細を捨てるため、元のロック解放失敗の根本原因は未確定。次は失敗位置と安全な例外分類の記録を追加する。DO RPC記録の47件対49件の差だけでは失敗したactionは特定できない。
- 解放診断の実装・反映: E2E専用応答へ失敗位置・固定例外分類・解放と照会の判定を追加し、監査JSONLとrun manifestへ引き継ぐ。Python 739件・Node 237件と静的検査が成功。[実行35994329876](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35994329876)で診断版を専用Workerへ反映し、18段階と各verify、回収、`passed`・全manifest `dirty=false` を確認した。ロック解放失敗は再発せず、元の原因は引き続き未確定。障害分類・保存はローカル固定障害テストで確認済み。詳細は[検証手順](TESTING.md#google制御ロックの解放診断)を参照。
- 暫定対応: 未対応の通常同期、共有状態と全件適用を伴う通常Webhook同期、通常ジョブ route は `E2E_ORCHESTRATED_WRITES_ENABLED=false` で `404` にする。read-only preflight、service CRUD、所有資源限定のサービス間scenario、QA通知scenario、前日リマインドscenario、Notion期限cleanup scenario、Webhook simulation scenario、Google Webhook初回実配信scenario、Google変更起因Webhook scenarioは別routeで継続する。
- 7件・28stepの追加: 2件の予定を追加し、`maxResults=2` の実ページ送り、残件5→3→1→0、Notion更新失敗とDiscord削除失敗の固定注入・次HTTPでのqueue回復を実装した。所有確認・cursor保護・部分反映・既存ID維持・途中回収・旧5件run互換性をローカル検証済み。専用環境での実行・回収・証跡照合は未完了。
- 完了条件: 全下流資源と状態を強整合 manifest で所有し、run ID と対象 fingerprint の一致後だけ cleanup できること。simulation と実 webhook / Cron 配信の証拠は分けること。
- 追跡: [GitHub Issue #17](https://github.com/lycanthr0pes/IE_Event_Bot_fork/issues/17)
