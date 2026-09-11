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

残作業と完了条件は [E2E-PLAN.md](E2E-PLAN.md) で追跡する。項目10の5件は2026-09-11のユーザー指定により追加対象外とし、過去の「未確認」記述は追加作業の指示として扱わない。

### 通常Discord同期の共有状態と排他

- 実環境で確認済み: 通常StateStoreをrun・scope別の固定2キーへ隔離し、DO所有manifestに基づく保存・別HTTP読戻し・回収を実装した。[実行34604249166](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34604249166)で各HTTP 200、version一致、回収後のoutcome=passedと全資源dirty=falseを確認した。外部fixtureとの接続と通常ポーリングは残作業である。
- ローカル修正済み: queue保存失敗時にsnapshotだけ進む問題をqueue先行保存へ変更し、手動・Cronの単独同期を全体同期と同じDOロックで保護した。作成・更新・削除の再試行、上限残件、並行HTTP、例外・キャンセル後の解放を代替KV・DO・外部APIで確認した。
- 未確認: 実KVの伝播遅延、ロックTTL超過、通常Guild全件の適用と実Cron。外部成功後の状態保存失敗では再実行され得る。複数キーの原子的更新と一度限りの適用は保証しない。

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
- 未対応範囲: 通常Google同期の共有cursorと全Calendarの全件適用、Discordの共有snapshot / queue、全体同期、通常watch更新、実Cron、通常QAジョブの全件取得と共有cache、通常リマインドのGuild全件取得と共有cache、通常Notion cleanupの内部DB全件取得と共有状態。
- 暫定対応: 未対応の通常同期、共有状態と全件適用を伴う通常Webhook同期、通常ジョブ route は `E2E_ORCHESTRATED_WRITES_ENABLED=false` で `404` にする。read-only preflight、service CRUD、所有資源限定のサービス間scenario、QA通知scenario、前日リマインドscenario、Notion期限cleanup scenario、Webhook simulation scenario、Google Webhook初回実配信scenario、Google変更起因Webhook scenarioは別routeで継続する。
- 完了条件: 全下流資源と状態を強整合 manifest で所有し、run ID と対象 fingerprint の一致後だけ cleanup できること。simulation と実 webhook / Cron 配信の証拠は分けること。
- 追跡: [GitHub Issue #17](https://github.com/lycanthr0pes/IE_Event_Bot_fork/issues/17)
