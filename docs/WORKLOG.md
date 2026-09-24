# 作業履歴

## 2026-09-24: 28step初回の確認通信失敗と回収、再試行を追加

- 承認後の[実行36001946180](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36001946180)はcommit `3eef09528767ff966f7df09901066934845c3f81` でLocal validationとdeployが成功し、step 0〜5のverify、step 6のadvanceまで200だった。続くverifyは27 msで `worker_request_failed`・status 0となった。NodeのfetchがHTTP応答を返す前に例外となったことは確認できるが、元の例外を保存していないためDNS・TLS・接続切断などの詳細原因は不明。Workerへの未到達や、以前のロック解放失敗との同一原因を断定しない。
- 回収は200。artifactの監査34行・17操作、run/version/commit一致、`failed_clean`・全manifest `dirty=false`、共有KVと所有資源の回収を独立照合した。7件・ページ送り・Notion更新／Discord削除の固定失敗段階は未到達であり、28step成功とは扱わない。
- MCPの通信例外に `transport_diagnostic` を追加し、fetch／本文読取り、固定の例外型名と許可した通信コードのみを応答・監査JSONL・run manifestへ残す。例外本文・URL・接続先アドレス・任意文字列は保存しない。
- Googleのverifyだけ、応答前の通信失敗または200応答の本文読取り失敗に3秒間隔・最大3回の試行を追加した。同runのみ許可し、既存の状態反映待ちを含む合計25回の上限を維持する。prepare／advance、アプリケーションエラー、run不一致は再送せず回収へ進む。
- 通信再試行の再現テスト4件が修正前に失敗し、修正後の境界テスト9件が成功。Node全256件が成功した。実環境での修正版再実行は未完了。

## 2026-09-24: Google同期の7件・ページ送り・部分失敗E2Eを実装

- matrixを28stepへ拡張した。従来の18段階後に予定を2件追加し、削除済み2件を含む計7件を `maxResults=2` の通常ページ送りで取得する。4ページ以上と所有7件を必須にし、上限2件で共有queueを5→3→1→0へ消化する。
- 所有Notionページの更新失敗と所有Discord予定の削除失敗を固定注入する。通常適用の失敗分岐、片側の部分反映、対応表とcursor・最終成功時刻の維持、次のHTTPでqueueだけを処理する回復を検証する。既存IDの変更を拒否し、失敗値を返す対象操作以外は実APIへ接続する。サービス障害の観測とは区別する。
- 通常処理に任意callbackを渡せる接続点を追加したが、指定しない場合のAPI呼出し・ページサイズ・同期動作は維持した。KV・DO binding、通常HTTP/Cronの入口、機密設定は変更していない。`matrix_extended` の途中変更を拒否し、旧5件runの完了・回収条件も維持する。
- Python 754件、Node 243件、Ruff、Pyright、E2E設定・機密保護・workflow検査が成功。ページ途中の503・欠落・重複に対する書込み拒否、追加段階の途中回収、既存削除履歴100件、旧5件run互換性も確認した。テスト用Discord ID生成は削除後も既存IDを再利用しないよう修正した。
- 実サービス実行・回収・artifact照合は未完了。これまでに確認した18stepの成功記録と、ロック解放失敗の根本原因未確定という境界は保持する。

## 2026-09-24: 解放診断版の18段階E2Eと回収を確認

- 承認後の[実行35994329876](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35994329876)は成功。commit `538008a2cb40675e81e8476ca9ee7316b8e285cd` を専用Workerへ反映し、run `E2E-20260924T121804Z-12a346a4` の18段階と各verifyがすべて成功した。
- artifactを独立照合し、監査78行・39操作、run／version tag／deployと最終version fingerprint／commitの一致、clean checkoutを確認した。Discordの意図した400拒否4件、cursor保護、queue再試行、繰返し親と所有予定・Notion・Discord・共有KVの回収が記録され、`outcome=passed`・全service/scenario manifest `dirty=false` だった。
- Local validation、Wrangler dry-run、JUnit 739件（失敗・エラー・skip 0件）を確認。今回はロック解放失敗がなく `release_diagnostic` は出力されていない。診断版の反映と正常系の実環境検証は完了したが、障害時の分類・保存はローカル固定障害テストでの確認であり、元の実障害原因の確定・修正とは扱わない。

## 2026-09-24: Google制御ロックの解放失敗を分類して保存

- E2E専用の解放処理に `release_diagnostic` を追加し、release／status RPC、応答不正、owner残留を区別した。例外は固定分類のみとし、本文や識別子は返さない。MCP応答・監査JSONL・run manifestまで許可した値だけを保持する。
- 既存のHTTP 409・dirty維持・解放と照会の順序・TTLを保持した。強制解放や再試行は追加していない。通常WorkerとDOの実装・設定は変更していない。
- Python 739件、Node 237件、Ruff、Pyright、E2E設定・機密情報保護・workflow検査が成功した。固定例外と不正応答の診断、ロック残留時のcleanup拒否、TTL経過後の回収、診断のJSONL／成果物への保存をローカル確認した。
- 実環境への反映・18段階の再検証は未完了。過去のロック解放失敗の原因は未確定であり、今回の変更は次の失敗箇所を記録するためのもの。

## 2026-09-24: ログ照会403が解消し、過去ログを確認

- account tokenの権限更新後、[診断35991322986のattempt 2](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35991322986/attempts/2)が成功した。実行commitは `6a2674b4575d3425600fa661ae07484cea8999de`。同じ診断コードでアクセス拒否が解消したことを確認した。デプロイ・外部fixture操作は行っていない。
- artifactを独立確認し、対象Workerの失敗時間帯123件・成功時間帯89件、両時間帯のページ取得完了を確認した。失敗時間帯のerrorレベル8件は直後のcleanup時刻に一致する。既定の固定分類器に一致するRPC／storage／CPU／memory等の異常文字列は0件、全イベントのruntime outcomeはok。ただし、アプリが409／503を返した事実を取り消すものではなく、固定分類器の非一致で全異常を除外することもできない。
- step 11のHTTP処理時間帯にあるDO jsrpc記録は失敗時47件・成功時49件。末尾のreleaseとstatusの2呼出し差と整合するが、保存したログにはrequest／DO相関IDとRPC actionがないため、欠けた呼出しの同定や原因確定の証拠とは扱わない。
- 原因調査の到達点: 制御ロック解放後の検査区間で失敗し、その後のacquire拒否が続いた。元のコードはrelease RPC・status RPC・応答／owner検査の例外を一律の `google_sync_release_failed` に変換して原例外を記録しない。今回の過去ログから原例外は復元できず、プラットフォーム障害・RPC失敗・不正応答のどれかを断定できない。次の作業は、失敗位置・安全な例外分類・owner一致の真偽を記録する診断を追加し、再発時に判別可能にすること。元の障害の修正・再発防止完了とは扱わない。

## 2026-09-24: 権限を編集したtokenと使用中tokenの相違を確認

- ユーザーは権限追加を「マイプロフィール → APIトークン」で行ったと回答した。診断35991322986で使用したのはaccount用verifyが200・activeとなるアカウント所有tokenであり、変更対象の個人tokenと所有形態が異なる。個人tokenへの権限追加を、GitHubが使用するaccount tokenへの反映と扱うことはできない。
- 次は「アカウントの管理 → アカウントAPIトークン」にある、GitHub Environment `e2e` の `CLOUDFLARE_API_TOKEN` に対応するtokenの権限を確認する。使用中account tokenへの設定反映と再診断は未実施。これで元のロック解放失敗の根本原因が確定したわけではない。

## 2026-09-24: Telemetry権限の追加案内を撤回

- ユーザーの画面には `Workers Observability Telemetry` の設定項目が存在しない。旧権限一覧の名称を現在の設定画面で選択可能と推測した案内は誤りとして撤回する。既存の「Workersの可観測性 編集」だけでは不足すると断定しない。
- 診断で使われたのは有効なaccount token。編集したtokenが「アカウントの管理」のaccount tokenか、「マイプロフィール」のuser tokenかを確認する。403・code 10000だけで両者の不一致を断定せず、同条件の再実行や追加権限の要求は行わない。

## 2026-09-24: account tokenの有効性とログ照会拒否を分離

- [追加診断35991322986](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35991322986)はcommit `6a2674b4575d3425600fa661ae07484cea8999de` で実行され、ログ照会がHTTP 403・code 10000・authentication分類で拒否された。
- 同じtokenの対象account用verifyはHTTP 200・success=true・status=active。user用verifyの401・code 1000と合わせ、使用中のaccount tokenが有効であることを確認した。ログ照会権限や個別Workerへの許可までは証明しない。過去ログと元のロック解放失敗の根本原因は引き続き未取得・未確定。
- ユーザー報告では既存tokenへ権限追加済み。現在の権限名・Read/Editの確認を依頼した。[公式の権限一覧](https://developers.cloudflare.com/workers/authorization/workers/#legacy-workers-permissions)には `Workers Observability Edit` と `Workers Observability Telemetry Edit` が別項目として掲載されているため、両者を同一項目と推測しない。設定の確認前に同条件の診断を繰り返さない。

## 2026-09-24: 権限追加後もログ診断403が継続

- ユーザーは既存トークンへ権限を追加したと報告した。[診断実行35987968532のattempt 2](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35987968532/attempts/2)も `diagnostic_http_403` で停止し、過去ログは取得できなかった。
- 診断側の情報不足を補うため、APIエラーの数値コード・固定分類をartifactへ追加した。401/403時だけ、使用中トークンをuser用・対象account用のverify APIで検証し、HTTP statusとactive／disabled／expiredだけを残す。生の本文・token値・token IDは保存しない。片方のverify拒否だけで無効とは判定せず、verify成功もログ閲覧権限の証明とは扱わない。
- Node 236件、エラー本文の非出力・user/account両所有形態・不正応答のテストが成功した。Worker実装・デプロイ・外部fixture操作・権限変更は含めない。元のロック解放失敗と403の詳細原因は未確定。

## 2026-09-24: Googleロック診断のログ照会が403で停止

- ユーザー承認後の[診断実行35987968532](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35987968532)は、commit `49fc180a77a0c18d669699ed4c41c7754a211bfe` のLocal validationに成功し、ログ照会で失敗した。artifactの `report.json` は `diagnostic_http_403`。過去ログは取得できていない。
- Cloudflare APIによるアクセス拒否までは確定したが、403だけではトークンの権限不足・対象アカウント・その他の利用制限を区別できない。照会に必要な `Workers Observability Write` と対象アカウントの許可を、GitHub Environment `e2e` の `CLOUDFLARE_API_TOKEN` に対応するCloudflare側設定で確認する必要がある。トークン値の読取り・権限変更は行っていない。
- run ID作成・deploy・fixture操作・cleanupの各stepはskipされた。元の `google_sync_release_failed` の原因は未確定のまま保持する。設定確認前の同条件再実行は行わない。

## 2026-09-24: Google matrixのロック解放失敗を調査

- 失敗runのstep 11は09:26:45.483〜09:26:58.359 UTCの12.876秒だった。phase上限90秒・制御ロックTTL300秒への到達とは一致しない。後続cleanupは約10秒以内の8回すべてbusy。成功runの同stepは10.966秒だった。
- コードは制御DOのrelease RPC・status RPC・応答確認を同じ `google_sync_release_failed` にまとめ、例外詳細を残さない。代替API・実DOロジックでstep 11に固定障害を入れ、releaseの削除前例外／不正JSONはrelease_failedと後続busyを再現した。正常release後のstatus例外／不正JSONではrelease_failedとなるが回収は成功した。4ケース成功は原因候補の比較であり、実環境の根本原因の確定ではない。
- `read-only-google-lock-diagnostics` を追加した。専用Workerの失敗35秒・成功19秒の2時間帯だけをCloudflare Workers Logsへ照会する。保存なしのqueryを使い、artifactには固定エラー分類・時刻・実行時間・HTTP statusだけを含め、生ログ・URL・認証情報・例外本文を含めない。デプロイ・E2E run作成・外部fixture操作は実行しない。既存GitHub Environment `e2e` の承認を維持する。
- [Cloudflare Query API](https://developers.cloudflare.com/api/resources/workers/subresources/observability/subresources/telemetry/methods/query/)は照会にも `Workers Observability Write` 権限を要求する。既存tokenでの利用可否と過去ログの取得は未確認。取得できない場合はその状態を報告し、権限を自動変更しない。

## 2026-09-24: Google matrix 18段階の再実行が成功

- [再実行35982356318](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35982356318)で全18段階と各verifyが成功した。3日間の終日・UTC日跨ぎ予定、残存3件それぞれのDiscord HTTP 400・code 50035、cursor保護、上限1件による共有queueの3→2→1→0の消化、既存対応IDの維持を確認した。
- commit `f09ba2933196e274c0214b4b9d0628316255f44e`、run `E2E-20260924T094010Z-6c1c180a`、Worker version fingerprint `d26edefa263cac2162c0d0bf4f50d189a783a086d4e87fcc98350126d6c23004` を照合した。監査78行・39操作、全18段階の読戻し、通常とalwaysのcleanup各1回、JUnit 719件・失敗0をartifactで独立確認した。今回runは `passed`、全service/scenario manifestは `dirty=false`。繰返し親・各予定・Notion page・共有KVの回収を確認済み。
- 結果をE2E計画・標準文書へ反映した。前回のロック解放失敗は今回再現しなかったが、原因解明・修正完了とは扱わない。任意件数・構成、サービス障害の観測、通常Cron、本番反映はこの実行の保証に含めない。

## 2026-09-24: Google matrixの残存資源を回収

- [回収実行35981499346](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35981499346)はcommit `b7a978677bfdbc661cff53e26c93e46547dd5c05`、既存run `E2E-20260924T092101Z-dcf6de0a` で成功した。繰返し親とGoogle予定、Discord予定、Notion page、共有KVの回収stage、`failed_clean`・全service/scenario manifestの `dirty=false` を確認した。
- 監査4行・2操作、run/version/commit・clean checkout、JUnit 719件成功をartifactで独立照合した。Worker version fingerprintは `0c650740e7f58be2685375175c7e25db917ba3743be19988270c9d2ae4b8a044`。新規fixtureは作成していない。
- 元の18step実行は失敗として保持する。ロック解放失敗の根本原因は未確定であり、回収成功を原因修正や18step成功と扱わない。専用環境で再実行する。

## 2026-09-24: Google matrixのロック解放失敗と回収経路

- [実行35980469928](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35980469928)はcommit `fa5fcd87072928ec414221204337fb958b5bcf08`、run `E2E-20260924T092101Z-dcf6de0a` でstep 0〜10のverifyに成功した。step 11のadvanceで `google_sync_release_failed`、続く回収8回は `google_sync_busy` となり、manifestはready・dirty=true。実行成功・回収完了とは扱わない。
- ロック解放失敗の根本原因はartifactだけでは確定できない。ロックTTLは300秒。既存回収workflowがreadyを拒否する条件を再現し、同run・version・他資源cleanの照合を維持してreadyから回収できるよう修正した。Node 227件成功。実環境の回収と18step再実行は未完了。
- 回収用commit `b7a9786` の[実行35981499346](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35981499346)はLocal validationに成功し、GitHub Environment `e2e` のrequired reviewer承認待ち。既存runだけを回収し、新規fixtureは作成しない。承認前のため、回収完了・dirty=falseは未確認。

## 2026-09-24: Google matrixの複数失敗と分割再試行を追加

- 3日間の終日予定とUTCの日跨ぎ予定を含む5件へ変更し、残存3件のDiscord入力拒否、共有queueを上限1件で3→2→1→0へ消化する18stepを実装した。各拒否の400とcursor保護、各HTTPの処理件数・残件・対応ID・部分反映を独立に照合する。
- 追加経路のテスト5件が実装前に失敗し、実装後に成功した。途中回収、想定外の成功・503応答の拒否と回収、件数1・2・5・17と上限1・2・5の12組をローカル検証した。Python 719件、Node 221件、Ruff・Pyright、依存import、E2E設定・workflow・secret hygiene検査、秘密情報を含まない追跡ソースのコピーと空envによるE2E Worker dry-run、文書相対リンク15件・差分検査が成功した。
- 実サービス実行は未完了。入力検証の400とサービス障害の観測は区別し、任意件数・構成の保証は追加しない。

## 2026-09-24: Google matrix E2Eの実サービス検証が成功

- [実行35977892750](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35977892750)で、終日1件・通常2件・繰返し2回の計5件、上限2件での繰越、日時・説明更新、個別削除、共有queueでのAPI拒否・再試行の全14段階と各verifyが成功した。
- 実行commit `1ca952b9a5297e4dcbe5de837ac0669d3af66a20`、run `E2E-20260924T085514Z-61014564`、Worker version fingerprint `5673c28f6fa825fd9044e02b2189d0514a170d3558d2feff896b4c8c01e5de0c` を照合した。監査62行・31操作はすべて成功し、checkoutはclean、JUnitは701件・失敗0だった。
- `google_matrix_step_0`〜`13`、全入力・cursor保護・繰返し親回収・共有KV回収の各stageは200、意図したDiscord入力拒否は400だった。通常とalwaysのcleanupが成功し、今回runの `outcome=passed` と全service/scenario manifestの `dirty=false` を確認した。
- 対応するE2E計画項目を完了にした。専用環境の有限5件・14段階の検証であり、任意件数・繰返し規則、サービス停止・回線断、通常Cronの検証は含めない。

## 2026-09-24: Google全件E2Eの予定形式・件数・共有再試行を拡張

- 通常2件・終日1件・繰返し2回の5件を14stepで処理するmatrixモードを追加した。Google発行のinstance IDを親ID・元時刻・markerで照合して固定し、作成をHTTP単位へ分割した。上限2件の繰越、日時・説明更新、終日と個別回の削除、共有KVでの400拒否・cursor保護・queueだけの再試行、親を含む回収を実装した。既存2件・3件モードは維持する。
- ローカルでは全段階、履歴100件、途中回収、日時の不一致、不正instance、親規則の変更、所有記録の差替え拒否を確認した。実サービスの成功は未確認。
- Python 701件・Node 218件、Ruff・Pyright、E2E設定・workflow・secret hygiene検査、E2E Workerのdeploy dry-runが成功した。

## 2026-09-24: Google共有KV・全件E2Eの実サービス検証が成功

- [実行35969469480](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35969469480)で、既存の削除履歴を保護した3件の全件適用、pending・drained・updated・deletedの全4段階と各verify、共有KVを含む回収が成功した。
- 実行commit `0c6a3825277cb95e2e2cdac307a23c2e6f5afec0`、run `E2E-20260924T072644Z-72ef81c2`、Worker version fingerprint `c6fdf2227376e392976ea3e5cb63ac6689569704bd4ca490340cb7cc23e69bb5` を照合した。監査22行・11操作はすべて成功し、checkoutはclean、JUnitは678件・失敗0だった。
- 今回runの `outcome=passed`、全service/scenario manifestの `dirty=false`、Google/Discord削除・Notion archive・共有KV回収stage成功を確認した。初回prepare_fullの監査上の所要時間は67.236秒で、従来50秒を超えて完了した。
- E2E計画の共有KV・全件モード検証を完了にした。既存履歴以外の3件を対象とする専用環境の検証であり、任意件数・繰返し予定の構成、外部APIの実障害、通常Cron、通常DOの最終成功時刻の検証は含めない。

## 2026-09-24: 全件E2Eの50秒タイムアウトと回収を確認

- [実行35968760516](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35968760516)は `be790581aaff3136f0fe032556d091541e013186` でdeploy・削除履歴照合・3件作成・初回dispatchまで成功し、phase完了前に `google_sync_timeout`（409）で停止した。runは `E2E-20260924T071821Z-3ed8ab4c`、Worker version fingerprintは `b69e4505698e2fa025954b1cc13f573c4e844bae13e6ff12fd0fb294c7dfed7c`。
- 監査8行・4操作、run/version/commit一致を確認した。通常・alwaysのcleanupが成功し、今回runの `failed_clean`、共有KV回収stage成功、全manifest `dirty=false` を照合した。全件4段階は未完了。
- 3件の作成・適用・読戻しを従来の50秒に収められなかったため、全件phaseだけ90秒へ延長した。Google同期routeのHTTP待機を120秒、workflowのGoogle同期MCP呼出しを180秒に揃え、通常同期ロック120秒・制御ロック300秒より前にphaseを打ち切る。通常2件モードのphase上限50秒は維持する。
- 61秒相当のphaseが旧設定で失敗するテストを先に実行し、修正後の成功・後続verify・回収を確認した。Python 678件・Node 210件、Ruff・Pyright、E2E設定・workflow・secret hygiene検査、E2E Workerのdeploy dry-runが成功した。実サービスでの再実行は別途記録する。

## 2026-09-24: 削除履歴のある専用Calendarの再利用を修正

- 削除履歴でprepare_fullが停止する4ケースを先に再現した。開始時に履歴のID・内容のハッシュをDOへ記録し、不変の履歴だけを通常適用から除外する。既存履歴は適用・queue・回収へ渡さず、通常予定・新しい履歴・内容変更や復元は拒否する。同じCalendarの連続2回実行、複数ページと取得順、履歴上限、manifestの差替え拒否をローカル検証した。実サービスの再実行結果は別途記録する。
- Python 677件・Node 205件、Ruff・Pyright、E2E設定・workflow・secret hygiene検査、E2E Workerのdeploy dry-runが成功した。

## 2026-09-24: Calendar診断で削除履歴のみの残存を確認

- [実行35965137690](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35965137690)は承認後に成功した。`83cd4b3f8b5585935a9c42721e94f68e393fa0f6`、run `E2E-20260924T070808Z-523c5621`、Worker version fingerprint `5a466bf93575bb477274ceaf4d938047c9c2f3b00ac3c076186ebfa8a30e6a5e` をartifactと照合した。
- 読取り結果はHTTP 200・`calendar_deleted`。取得対象の全ページに通常予定はなく、削除済み予定の履歴が残っていた。削除履歴も含めて空であることを要求する全件モードの開始条件に抵触する。履歴の作成元・件数は今回の診断では取得していない。
- 監査4行・2操作（deploy、inspect）、cleanupなし、全service/scenario manifestが前回実行と同一で `dirty=false`、JUnit 668件成功を確認した。予定・共有KVへの書込みはない。
- 現行ガードのまま全件E2Eを続けるには、削除履歴のない新規E2E専用Calendarへの切替が必要。全件適用の実サービス検証は未完了のままとする。

## 2026-09-24: Calendar開始条件の読み取り専用診断を追加

- 既存artifactにはCalendarの失敗理由を区別する情報がなく、接続済みCalendarツールにも対象fingerprintが一致するCalendarがなかったため、専用Worker自身で診断する経路を追加した。
- 全ページのstatusだけを読み、空・通常予定のみ・削除履歴のみ・混在・HTTP失敗を固定値で監査する。KV・manifest・外部予定への書込みとcleanupは行わない。通常全件モードの開始条件は維持した。
- Python 668件、Node 205件、Ruff・Pyright、MCP設定・workflow・secret hygiene検査、E2E Workerのdeploy dry-runが成功した。実環境の診断結果は別途記録する。

## 2026-09-24: 新規KVでの全件E2EはCalendar開始条件で停止

- ユーザーがE2E用STATE_KVを新規namespaceへ更新した。変更はE2E設定のIDだけで、`952f27f9a1c068661d25f196ffccf70e38decec0` として保存・pushし、[実行35963510311](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35963510311)を実行した。
- Local validation、承認後のdeploy、共有KVの空状態検査は通過した。run IDは `E2E-20260924T062252Z-08da9194`。続くCalendar検査が `google_sync_calendar_not_empty`（409）で停止し、新規fixture作成・全件適用には到達していない。
- このエラーはGoogle取得失敗と、削除済み予定を含む取得結果が空でない場合の両方に使われる。現状のartifactでは予定の残存・削除履歴・取得失敗を特定できない。KVの切替失敗と扱わず、空の専用Calendarと取得条件を次の確認対象とする。
- 通常とalwaysのcleanupは計8回とも前回runのclean記録との不一致で拒否された。監査20行・10操作、run/version/commit・clean checkout、JUnit 659件成功を照合した。全service/scenario manifestは `dirty=false` で、google_syncは前回成功runの記録を保持している。今回の新規所有資源はなく、共有値や外部予定を削除していない。
- 全件適用の実サービス検証は未完了。結果を成功や回収成功へ読み替えない。

## 2026-09-24: 全件E2Eは共有KVの開始条件で停止

- `ea6044e63dd329aebc95d55ed789cf4674519497` の[実行35962599536](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35962599536)を実行した。Local validation成功、Environment承認後のE2E Worker deployとversion照合が成功した。run IDは `E2E-20260924T060357Z-380a526d`。
- `prepare_full` は `google_sync_shared_not_empty`（409）で停止した。共有6キーの少なくとも1つに既存値があり、新規fixture・所有manifest・共有状態を書き込む前の拒否である。既存値の内容・由来は未確認で、削除していない。Calendar・Guild・DBの空状態確認と全件適用には到達していない。
- 通常とalwaysのcleanupは計8回とも `google_sync_run_mismatch`（409）だった。google_syncのmanifestは前回成功run `E2E-20260924T051801Z-2198a0b3` のclean記録であり、今回の所有記録はない。全service/scenarioのmanifestが `dirty=false` のままであることをartifactで確認した。今回の回収成功・全件適用成功とは扱わない。
- 監査20行・10操作（deploy 1、prepare 1、cleanup 8）、run/version/commit・clean checkout、JUnit 659件・失敗/エラー/skip 0を独立照合した。実行の結論はfailure。成果物は `test-results/google-full-35962599536/` に保存した。
- 再実行には共有6キーが欠損したE2E専用KVが必要である。Calendarの削除記録を含む空状態検査も引き続き必要。既存の共有値を所有証拠なしに回収する変更や、空状態検査の緩和は行わない。

## 2026-09-24: Google同期の共有KV・全件モードを実装

- 空の専用環境に3件を用意し、通常取得の全入力を間引かず適用する `prepare_full` を追加した。共有KVの固定6キー、4段階と別HTTP検証、所有外入力の拒否を既存probeへ接続した。
- KV書込み予定のdigestを先にDOへ保存し、途中失敗・応答喪失後の回収と未知の値の保護を追加した。DOはモード変更と他scenarioのdirty所有との併存を拒否する。
- MCP・手動workflow `deploy-and-google-full-smoke`、通常・失敗時cleanup、監査とstage照合を接続した。実サービス実行・デプロイ・PR・マージはこの変更に含まれない。
- 検証: Python 659件・Node 195件、Ruff、Pyright、依存import、MCP設定・workflow・secret hygiene検査、通常/E2E Wrangler dry-runが成功した。dry-runは秘密情報を含まない追跡ソースのコピーと空envで実行した。文書の相対リンク13件と差分を確認した。

## 2026-09-24: 不正日時のAPI拒否とqueue再試行を実サービスで確認

- `d184a2aac578b9f20e05ba0bc960726574880285` の[実行35959152201](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35959152201)で、Local validation成功とEnvironment承認後に専用Workerをdeployした。run IDは `E2E-20260924T051801Z-2198a0b3`。
- 所有予定2件の作成・繰越消化・更新・削除に続き、不正日時PATCHへのDiscord HTTP 400とcode 50035を確認した。通常dispatchの500、残件保持、cursor・最終成功時刻の不変、次のHTTPで保存queueだけを処理する回復と既存IDの再利用を確認した。
- prepare 1回・advance 5回・verify 6回、通常とalwaysのcleanup各1回が成功した。監査30行・15操作、run/version/commit・clean checkout、JUnit 642件・失敗0を独立照合し、`passed`・全資源 `dirty=false` を確認した。
- 実APIによる入力検証エラーと回復の証拠であり、Discordのサービス障害・回線断の観測ではない。共有名前空間と任意予定の全件適用、PR・マージ、本番デプロイはこの実行に含まれない。

## 2026-09-24: API拒否E2Eの残存資源を回収

- 修正版 `834930fd2a9d4a81105cf280a2813ba55aca1506` の[復旧実行35955045460](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35955045460)で、Environment承認後に専用Workerをdeployした。回収対象は元のrun `E2E-20260924T040614Z-fdf4a42c` のみで、新規fixtureは作成していない。
- `google_sync_empty_name_recovered=200` から、記録済みID・Guild・run markerが一致し、名前だけ空になったDiscord予定の回収経路を確認した。Discord削除204、Notion archive 200、Google削除204、KV回収200、`failed_clean`・全資源 `dirty=false` を確認した。
- 監査4行・2操作、対象run・commit・clean checkout、旧実行からのWorker version変更と新version fingerprintの一致を独立照合した。Local validation、回収、always後処理、成果物収集が成功し、JUnit 642件・失敗0を確認した。
- 元のAPI拒否試験は失敗として保持する。不正日時を使う修正版の6段階E2E、共有状態と任意予定の全件適用、本番デプロイは未確認である。

## 2026-09-24: API拒否E2Eの失敗と所有資源の回収対策

- `33f5e487efa99a56b93fd9285523712dc9f45732` の[実行35952552380](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35952552380)で、Environment承認後に専用Workerをdeployした。run IDは `E2E-20260924T040614Z-fdf4a42c`。作成・繰越消化・更新・削除と各verifyは成功したが、API拒否段階が `google_sync_partial_failure_mismatch` で失敗した。
- run内とalwaysの回収8回は `google_sync_discord_owner_mismatch` で失敗し、manifestは `cleanup`・`dirty=true` のままである。監査36行・18操作、run/version/commit・clean checkoutを独立照合した。回収済み、E2E成功とは扱わない。
- Discordが空名更新を受理したモデルで回収409をローカル再現した。旧API拒否段階に限り、DOに記録済みの2件目のID・Guild・run markerと空名を照合して回収できるよう修正した。ID・Guild・marker・名前の不一致は拒否する。新規試験は所有確認に使う名前と説明を変更せず、不正な日時によるAPI拒否へ切り替えた。
- `deploy-and-google-sync-recovery` を追加した。既存run・稼働tag・cleanup段階と他のdirty資源がないことを確認後、修正版を同じrun IDでdeployし、対象資源だけを回収する。新規fixtureは作成せず、`failed_clean` と全体preflightを必須にする。実環境の回収確認はこの記録時点で未完了。

## 2026-09-24: Google同期のHTTP失敗後の残件保持とAPI拒否E2E

- 通常dispatch・StateStore・HTTPラッパーを通す再現テストで、Notion照会・取得・作成・archive・Discord ID書戻し、Discord削除の403・429・503とsubrequest上限の19ケースが失敗した。失敗時のqueue・対応ID・cursor保護と未着手分の保存を修正し、空の新規取得から同じ資源へ再試行できることを確認した。Discord削除済み404とDiscord由来取消の互換性も確認した。
- 所有2件の既存E2Eへ、所有確認後のDiscord不正PATCH・HTTP 400/code 50035確認を接続した。DOに変更不可の要件を記録し、workflowはAPI拒否証跡を必須にする。旧manifestの回収互換を維持する。入力検証エラーは実サービス障害の再現とは区別する。
- ユーザーが既存E2E専用環境の使用と専用であることを確認した。共有名前空間と任意予定の全件適用は未実装・未検証であり、今回の所有資源限定の経路と分けて追跡する。
- Python全637件、Node全183件、Ruff・Pyright、E2E設定・機密ファイル追跡防止・workflow検査が成功した。機密ファイルを含まないリポジトリ内コピーで通常/E2E両設定のWrangler dry-runが成功した。
- この記録時点で実サービス実行・PR・マージ・本番デプロイは未実施。

## 2026-09-15: Google同期の部分失敗とqueue再試行を専用環境で検証

- `7336d0d96161f79d3b4b45df456f572d022c22c3` の[実行34866761198](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34866761198)で、Local validation成功とEnvironment承認後に専用Workerを1回deployした。run IDは `E2E-20260914T161000Z-f1f8e5f7`。
- 所有2件の既存4段階に加え、Notion更新後のDiscord失敗の固定注入、通常dispatchの500、残件1件、cursor・最終成功時刻の不変、Notion新内容/Discord旧内容を確認した。次のHTTPでは新規入力を空にして保存queueだけを処理し、既存IDへの反映と残件0を読戻した。
- prepare 1回・advance 5回・verify 6回で再試行なし。注入段階27.218秒、queue回復26.944秒、最長phaseはprepareの40.429秒だった。監査30行・完了15操作とmanifest、run/version/commit・clean checkout、JUnit 610件・失敗0を独立照合した。
- run内と `always()` のcleanup成功、passed・全資源dirty=falseを確認した。実APIとKV・DOを使用したが、Discord失敗は固定注入であり実障害・回線断の観測ではない。実行時点でPR・マージ・本番デプロイは未実施。

## 2026-09-15: Google→Discord部分失敗の残件保存とE2E再試行

- Discord APIの作成・更新失敗がNoneを返すとGoogle同期が成功扱いになり、cursorが進んで残件を保存しないことを2テストで再現した。同期有効時のID未取得を失敗とし、通常queueへ保存するよう修正した。Discord無効時の互換性も確認した。
- 既存の所有2件E2Eへretry_pending・retriedを追加した。Notion更新後のDiscord失敗をrequest内callbackで固定注入し、通常dispatchの500、残件1件、cursor・最終成功時刻の不変、部分反映を読戻す。次のHTTPでは入力を空にしてqueueだけを同じIDへ適用する。
- 注入欠落・回復失敗・再試行要件の巻戻し・再検証失敗を拒否し、未完了回収をfailed_cleanとする。MCP監査に6段階のstatusを保持し、workflowで注入証拠を必須とした。実環境の追加段階は未実行。
- Python全610件、Node全181件、Ruff・Pyrightが成功。実Discord障害・回線断の観測、削除失敗、Notion ID書戻し失敗、応答喪失時の重複作成は修正の保証範囲外。

## 2026-09-15: 通常Google同期E2Eの再実行が成功

- 修正版 `24c9e53609bfd5a4fc3d832ab3bbd64f8b91ddca` の[実行34862331643](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34862331643)で、Local validation成功とEnvironment承認後に専用Workerを1回deployした。run IDは `E2E-20260914T152925Z-e16dcc4a`。
- 所有Google予定2件の全ページ取得・通常dispatch・Notion/Discord適用、上限1件の繰越、残件消化、説明更新、削除・archive、各段階のcursor・対応表・queue・外部資源の別HTTP読戻しが成功した。prepare 1回・advance 3回・verify 4回で、読戻し再試行はなかった。最長phaseはprepareの36.058秒だった。
- 監査22行・完了11操作とmanifest、実行commit・clean checkout、run ID・version tag・deploy/最終version fingerprint、JUnit 602件・失敗0を独立照合した。run内と `always()` のcleanup成功、`passed`・全資源 `dirty=false` を確認した。
- 修正後のローカルPython 602件、Ruff、Pyright、E2E設定・秘密情報形式・workflow検査、E2E Wrangler dry-runも成功した。通常名前空間の状態、任意予定の全件適用、実Cron、外部API障害からの途中再開は検証対象外。実行時点でPR・マージ・本番デプロイは未実施。

## 2026-09-15: 通常Google同期E2Eの初回失敗とKV欠損値の修正

- `ddbc8c8f112819c4b3234ba01eeedf705c3d934e` の[実行34861663237](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34861663237)で専用Workerを1回deployした。prepareは約17.7秒で409 `google_sync_failed` となり、後続段階へ進まなかった。実行時の例外種別は未確定である。
- run内と `always()` のcleanup成功、`failed_clean`・全資源 `dirty=false` を確認した。監査8行・完了4操作とmanifest、run ID `E2E-20260914T152309Z-cd452e5b`、version・commit・clean checkout、JUnit 600件・失敗0を独立照合した。
- KV欠損のJS null/undefinedをhash対象にすると同じ固定エラーになることを2テストで再現した。通常StateStoreと同様に欠損値へ正規化し、対象21テストの成功を確認した。作成完了とdispatch応答の安全な段階記録も追加した。実環境での修正確認は再実行で行う。

## 2026-09-15: 通常Google同期E2Eの所有・回収と手動workflowを接続

- `google_sync` の所有2件、run別KV6キー、DO所有・段階検査を実装した。通常dispatch・全ページ取得・適用を通し、繰越・消化・説明更新・削除を別HTTPで進め、cursor・対応表・queue・外部資源を読み戻す。
- 作成前の着手記録、応答喪失時のmarker再発見、所有不一致・ID衝突の拒否、回収再試行、未完了phaseの再送拒否、再検証失敗時の成功取消しを追加した。通常Workerの同期処理・設定・Cronは変更していない。
- 外部削除後にKV回収が失敗すると、最小tombstoneを所有確認できず再回収できない条件を1テストで再現した。削除着手をDOへ先に保存してからDELETEするよう修正し、再現テストの成功を確認した。一覧への反映が遅れても作成API由来の対応IDで読戻し・回収するケースも追加した。
- MCP固定ルート、`deploy-and-google-sync-smoke`、段階ごとの有限verify待機、version照合、通常と `always()` の回収・マスク済み監査へ接続した。
- Python全600件、Node全176件、Ruff、Pyright、E2E設定・秘密情報形式・workflow検査、通常/E2E Wrangler dry-runが成功した。Wranglerには空の専用envファイルを明示し、ローカル機密ファイルの自動読込みを避けた。
- 実サービスE2E・PR・マージ・デプロイは未実施である。既存の未コミットの保証範囲テスト・文書変更を保持した。

## 2026-09-14: 状態障害の保証範囲と通常Google同期の接続を検証

- `test_sync_guarantee_boundaries.py` に8ケースを追加し、両キーが古い場合・旧形式の削除待ち喪失、重複適用・通知、投稿応答と状態保存の失敗、owner確認後の保存競合を固定注入で再現した。通常処理の制限を保証範囲表へ整理し、E2E計画項目3の境界確定を完了にした。
- 初期の喪失モデルでは空値の同値保存が省略され、後で新しいqueueを読むと再試行できた。別イベントの失敗残件でqueueを更新する条件へ修正し、元の削除待ちが実際に失われることを確認した。
- `test_google_sync_pipeline.py` に通常dispatch・全ページ取得・通常適用・状態保存を通す3ケースを追加した。件数上限・残件・対応表・更新・取消・照会例外の再試行・取得失敗時のcursor保護を代替APIで確認した。
- 検証: Python全581件、Ruff全体、`.venv` 有効化後のPyright（エラー0）、変更文書の相対リンク10件、`git diff --check` が成功した。型検査で指摘された新規テストのpayload検査追加後、対象3件とRuffを再確認した。
- アプリケーションの挙動は変更していない。実サービスE2E、実サービス用Googleシナリオの所有・回収・workflow接続、PR・マージ・デプロイは今回未実施。

## 2026-09-14: PlantUMLを手動実行のみに変更

- ローカルに保持していた `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の手動化変更を公開対象に含めた。`push`・`pull_request` を削除し、`workflow_dispatch` を保持した。
- Java、固定JARとSHA-256検証、ALLOWLIST、図の検証・SVG生成・artifact保存のジョブ本体が変更されていないことを比較した。相対リンクと `git diff --check` を確認した。図の生成処理自体は実行していない。
- upstreamのmainにも同じ自動起動があるため、起動条件だけを変更するPRで反映する。forkのmainにはPlantUML workflowは存在しない。

## 2026-09-14: 分割した状態障害E2Eを実KV・DOで再検証

- 修正をfork作業ブランチ `feature/sync-fault-request-split` の `c1740e2f0a5d11dedefe4c06df24f318110ef1f2` へcommit・pushし、[実行34841715250](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34841715250)を起動した。Local validation成功とEnvironment承認後、専用Workerを1回deployした。
- prepare 1回・advance 7回・verify 1回で、KV固定障害7ケース・TTL超過1ケース・別HTTP読戻しがすべて200となった。最大ケース15.191秒、TTLケース14.051秒、verify 13.133秒で、読戻し再試行はなかった。
- artifact監査24行・完了12操作とmanifest、run ID `E2E-20260914T120901Z-7c8d0e77`、version tag・fingerprint、対象commit・clean checkout、JUnit 570件・失敗0を独立照合した。run内と `always()` のcleanup成功、`passed`・全資源 `dirty=false` を確認した。
- 古い値と保存失敗は固定注入で、外部同期は代替runner。実KV伝播遅延・実サービス障害・実Cron・別Workerリクエスト間の競合は証明しない。実行時点で修正は未マージ。本番デプロイは未実施。
- 計画・検証文書・課題・変更履歴へ結果を反映した。開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-14: 状態障害E2Eの時間上限対策

- マージ済み `76ac4c8` の[実行34839754885](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34839754885)は、Local validation成功とEnvironment承認後に専用Workerを1回deployした。run IDは `E2E-20260914T114605Z-60928ebf`。7つのKV障害ケースのprepare処理を記録したが、約51.4秒で409 `sync_faults_probe_failed` となり、TTLケース・別HTTP読戻しは完了しなかった。50秒のphase上限への到達が疑われるが、旧エラーでは例外種別は確定できない。
- run内と `always()` のcleanupが200、対象 `failed_clean`・全資源 `dirty=false` を確認した。artifact監査8行・完了4操作とmanifest、version・run・commit一致、JUnit 563件・失敗0を独立照合した。失敗した検証をpassedとして扱わない。
- 1回prepareと7回advanceで、8ケースを1 HTTPずつ実行するよう変更した。DOに着手と確定位置を保存し、hashの固定順・1件ずつの追加を強制する。未完了verifyは進捗を変更せず拒否し、書込み途中の失敗は再実行せず回収する。timeoutを固定エラーへ分離した。
- 分割前に再現テスト2件の失敗を確認してから修正した。合算60秒の遅延モデル、途中timeout後の再実行拒否、所有者・version・認証の不一致、ケース飛越し、workflowの早すぎる完了・完了不足と必須cleanupも確認した。
- Python 570件、Node 166件、Ruff、Pyright、E2E設定・Secret hygiene・workflow・Bash構文検査が成功した。秘密ファイルを含まないコピーで通常・E2E設定のWrangler dry-runが成功した。
- 時計とsleepを置換しないローカル実行でもprepare・advance 7回・verify・cleanupが成功した。KVケースの最長は6.31秒、TTLケースは10.03秒だった。KV・DO storage・runtimeは代替実装、外部通信は遮断しており、分割後の実Cloudflare動作は未検証。今回の修正のPR・マージ・再デプロイは未実施。
- 開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-14: 古いqueueによる未処理操作の喪失を対策

- 作成・更新・削除・件数上限の繰越・通知待ちの5ケースで、古いqueueの読取り後に残件が失われることを失敗テストで確認してから修正した。snapshotの各イベント指紋にも `_pending_sync` を保存し、見えているqueueを優先してsnapshot側だけに残った操作を補完する。既存のKVキーとDOの責務は維持した。
- 投稿済みmessage IDを保持し、異なる通知先・message IDの競合は停止する。削除待ちはsnapshotに残して復元するが、観測済みイベントの比較からは除外する。旧形式の読取り互換と処理順を維持した。
- E2Eのsnapshot所有権検査を埋込み残件にも適用した。通知シナリオでは適用時のqueueも期待値と照合し、古い読取りから予定外の外部処理へ進むことを拒否する。`stale_queue_loss` は残件回復と `pending_lost=false` を必須条件に変更した。
- Python 563件、Node 163件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、Bash構文検査が成功した。秘密ファイルを含まないコピーで通常・E2E両設定のWrangler dry-runが成功した。
- 時計とsleepを置換しないローカル実行は28.96秒でprepare・verify・cleanupが成功した。外部通信を遮断し、KV・DO storage・Workers runtimeは代替実装である。実CloudflareのE2E、PR・マージ、Release・本番デプロイは未実施。
- 両方のキーが残件生成前の値を返す場合、残件情報のない旧snapshotと古いqueueの組合せ、再適用・重複配信は保証範囲外として文書化した。開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-14: 状態障害・TTL超過の検査と旧結果保存の拒否

- `sync_faults` を追加し、古いsnapshot / queueの固定3ケース、外部適用の代替runner成功後のqueue / snapshot保存前後失敗4ケース、手動同期の10秒TTL超過1ケースを検証できるようにした。prepare中の読取りは注入モデル、書込みと別HTTPのverifyは所有KVを使う。
- TTL超過後に旧実行が成功結果を保存する不具合を7テストで再現し、全7件の失敗を確認してから修正した。通常の手動・Cron分岐・全体同期は段階間でDOの期限・ownerを再確認し、失効・確認不能なら409で停止する。Google適用後のcursor・後続同期も止める。同期本体内部の書込み、DO確認とKV書込み間の競合、外部処理の取消しは保証しない。
- 古い空queueと最新snapshotによる未処理1件の喪失を固定モデルで再現した。古い値・保存失敗による再適用も確認した。これらは解決済みとせず、`passed` は既知の限界を含む期待挙動の確認であることを計画・課題・テスト文書へ明記した。
- run・scope・対象と8ケースの証拠hashをDOで所有し、48候補KVキーを限定する。認証・version照合、MCP、`deploy-and-sync-faults-smoke`、有限verify待機、失敗時と `always()` の回収へ接続した。回収と制御ロック解放を確認後にcleanとし、失敗時はdirtyを維持する。
- Python 546件、Node 163件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、Bash構文検査が成功した。秘密ファイルを含まないコピーで固定Wrangler 4.127.1の通常・E2E両設定のdry-runが成功した。
- 時計とsleepを置換しないローカル実行も21.60秒でprepare・verify・cleanupが成功した。外部通信を遮断し、KV・DO storage・Workers runtimeは代替実装であり、実Cloudflareの証拠ではない。新シナリオの実KV・DO実行、PR・マージ、Release・本番デプロイは未実施。
- 開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-14: 通常同期の共通ロック競合を実KV・DOで検証

- upstream [PR #72](https://github.com/ichipiro/IE_Event_Bot/pull/72)とfork同期[PR #58](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/58)のマージ後、fork `develop` の `f0a342e1965bbd086d4ff2ed3834673a3475a7cc` で[実行34834547224](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34834547224)を実行した。Local validation成功後、Environmentのrequired reviewer承認を通して専用Workerを1回deployした。
- `sync_lock` の6 roundで、手動・CronのDiscord同期分岐・全体同期の共通ロック処理を通す競合拒否、結果KV保護、成功・固定例外後の解放と再実行を実KV・DOで確認した。結果8キーのhashと未作成キーの別HTTP読戻し、固定18キーの回収も成功した。
- artifact `e2e-evidence-34834547224-1` の監査10行・完了5操作をmanifestと独立照合した。run ID `E2E-20260914T104430Z-8bced83f`、Worker version tag、deployと最終version fingerprint、対象commitが一致し、実行checkoutはcleanだった。
- 6 round、結果読戻し、KV回収の各200、run内と `always()` のcleanup成功、`outcome=passed`、全資源 `dirty=false` を確認した。prepare / verifyは各1回で、KV読戻し再試行は発生していない。artifactのfingerprint構造と、生URL・認証情報・生owner/資源IDの不在を確認した。
- Actions両jobが成功し、JUnit artifactの510件・失敗0・エラー0・skip 0を独立照合した。競合は1 HTTP内の共通処理の並行呼出しであり、同期本体は検査用runnerである。別Workerリクエスト間の競合、実Cron配信、外部API適用中の競合、TTL超過は検証していない。Release・本番デプロイは実施していない。
- E2E計画・検証文書・課題・文書変更履歴へ結果を反映した。開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-14: 通常同期の共通ロック競合E2Eを実装

- `sync_lock` を追加した。手動・CronのDiscord同期分岐と全体同期の共通処理で、各経路を保持側とする成功・固定例外の6 roundを実行する。保持中に3経路を競合側として呼び、本体未実行・結果KVアクセスなし・owner維持、保持側の解放と例外後の成功を確認する。
- 同期本体だけを省略可能なrunnerで隔離した。通常HTTP・Cronは既存runnerを使う。E2Eでは外部サービスへ通信せず、結果StateStoreをrun・scope・round別の固定キーへ限定する。DOに結果hashを保持し、別HTTPで実KVを読戻す経路を追加した。
- 18候補中の結果8キーを保存し、cleanupは固定18キーを回収する。所有情報・hashの差し替えとclean後の再利用をDOで拒否する。制御DOロックの解放読戻し後だけcleanにし、globalロックの強制解放はしない。途中失敗・タイムアウトでは保持taskのfinally完了を待つ。
- 認証・POST・version tag照合、MCP固定経路、`deploy-and-sync-lock-smoke`、有限の読戻し待機、監査対象の `always()` cleanup、statusとマスク済みmanifestへ接続した。
- Python 510件（新規23件）、Node 154件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、Bash構文検査が成功した。秘密ファイルを含まないコピーで固定Wrangler 4.127.1の通常・E2E両設定のdry-runが成功した。
- 実KV・DOでのE2Eは未実行である。競合は1 HTTP内の共通処理の並行呼出しであり、別Workerリクエスト間の競合、実Cron配信、外部API適用中の競合、TTL超過は証明しない。開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-14: 通常ポーリングの通知・再試行を実サービスで検証

- upstream [PR #70](https://github.com/ichipiro/IE_Event_Bot/pull/70)とfork同期[PR #56](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/56)のマージ後、fork `develop` の `b95be41d1e7c31f5d707168650f644caa10968c7` で[実行34831533775](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34831533775)を開始した。Local validation成功後、Environmentのrequired reviewer承認を通して専用Workerを1回deployした。
- `discord_batch_notification` で所有2件を通常ポーリングへ渡し、上限1件のNotion反映・通知投稿、固定注入後の同じmessageへのリアクション再試行、2件目の通知・最終読戻しが成功した。prepare 1回、advance 2回、verify各段階1回で、KV読戻し再試行は発生していない。
- artifact `e2e-evidence-34831533775-1` の監査18行・完了9操作をmanifestと独立照合した。run ID `E2E-20260914T100849Z-122a8828`、Worker version tag、deployと最終version fingerprint、対象commitが一致し、実行checkoutはcleanだった。
- 通知削除204・削除後GET 404、Discordイベント削除204、Notion archive 200を各2件、固定2KVキーの回収200、run内と `always()` のcleanup成功、`outcome=passed`、全資源 `dirty=false` を確認した。生URL・認証情報・生の外部資源IDを含めないartifact構造を確認した。
- Actionsの両jobと全必須stepが成功し、JUnit artifactの487件・失敗0・エラー0・skip 0を独立確認した。初回リアクション失敗はAPI呼出し前の固定注入であり、Discord側の実障害、実Cron、TTL超過は検証していない。Release・本番デプロイは実施していない。
- `E2E-PLAN.md`、`TESTING.md`、`ISSUES.md`、文書変更履歴へ結果を反映した。開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-14: 通常ポーリングの通知所有・再試行E2Eを接続

- `discord_batch_notification` を追加した。所有2件を上限1件で通常ポーリングへ渡し、1件目の投稿後にリアクション処理をAPI呼出し前で1回だけ失敗させる。別HTTPで同じmessageへ再試行し、残り1件の通知へ進む。
- 投稿前の着手・本文hash、投稿後のmessage ID、リアクションと回収の完了をDOに保持する。snapshot / queueはrun・scope別の通常KVへ保存し、所有外のID・通知先と古いqueueによる再適用を拒否する。
- 所有messageの削除・GET 404、event・page・KVの回収まで接続した。投稿応答・DO保存・KV保存の失敗、曖昧な検索、通知先や本文変更、部分回収失敗では所有記録を保持する。検索は直近50件に限定し、所有が解決しなければdirtyを残す。
- MCP固定経路、専用手動モード、各段階の読戻し、監査に基づく `always()` cleanupへ接続した。通常通知の既定処理、既存Googleシナリオ、追加対象外の項目10は維持した。
- Python 487件（新規通知E2E 34件）、Node 145件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、Bash構文、Markdown相対リンク、`git diff --check` が成功した。秘密ファイルを含めない作業用コピーで固定Wrangler 4.127.1の通常・E2E両設定のdry-runが成功し、コピーを回収した。
- 実サービスへの通知・デプロイ、GitHub Actions実行、PR・マージは未実施。固定注入はDiscord側の実障害を再現するものではない。開始時からの `.github/workflows/plantuml.yml` と `docs/REFERENCES.md` の変更を保持した。

## 2026-09-12: 通常Discord同期の作成通知繰越・再試行を修正

- upstream PR #68をmerge commit `2abc35331ce50051f69ddca3947c94a52cea945e`、fork同期PR #55を `510143f38111ece77987816baac88bef8f8eb370` でマージした。両PRのCI 4件成功を確認した。
- 件数上限の繰越・同期失敗後の通知漏れ、投稿・リアクション失敗時の再試行漏れを4件の失敗テストで再現した。queueに通知先と投稿済みmessage IDを保持し、通知だけの再試行を追加した。
- 通知待ちの完了イベントが一覧から消えたときの同期先保護を追加テストで確認し、保留通知だけを取り除くよう修正した。変更・削除・件数上限・通知先変更・旧queue互換も含め、新規22件が成功した。
- Python 453件、Node 133件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、秘密ファイルを含めないコピーでの通常・E2E設定のWrangler dry-runが成功した。
- 実サービスへの通知は未実施である。所有メッセージの記録・回収と専用workflow接続を次の作業に残す。投稿結果を取得できない場合やKV保存失敗・古い値の参照による重複配信は保証範囲外で、項目10の追加対象外指定は維持する。

## 2026-09-12: Googleを含む通常ポーリングを実サービスで検証

- [実行34619150601](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34619150601)で実装commit `5d4a12bc40950b3e5a844db3355d04602edf119e` を専用Workerへ1回deployした。所有2件の通常ポーリングをGoogle・Notionへ接続し、対応ID・上限1件・別HTTP残件消化・最終読戻しが成功した。
- artifactの監査7操作、Google/Discord削除204・Notion archive 200各2件、KV回収200、Worker version・run一致、outcome=passed、全資源dirty=falseを独立照合した。prepare / advance各1回、verify各段階1回で、KV読戻し再試行は発生していない。
- Actionsのローカル検査と専用E2E jobが成功し、JUnit 431件・失敗0を独立照合した。障害注入はローカル検証の範囲であり、通知・実Cron・TTL超過は後続作業である。
- 最初の実行34619021777はコミット件名の規約修正のため環境承認前に停止した。Approved E2E jobのstepは未開始で、デプロイ・外部書込みは実行していない。コード内容を変えず、修正後のcommitで上記実行を行った。


## 2026-09-12: 通常ポーリングのGoogle反映を追加

- upstream PR #67とfork同期PR #54をマージした。
- `discord_batch_google` を追加し、所有2件の通常ポーリング・上限1件・別HTTP残件処理をGoogleとNotionへ接続した。Google固定ID・Calendar・作成着手・回収完了をDOに記録し、対応IDを別HTTPで検証する。
- Google/Notion作成応答の喪失、ID衝突、KV保存失敗、Google削除失敗、所有権不一致、認証失敗後の成功取消しを代替APIで確認した。Google認証の通常KVキャッシュは使わない。直接tokenがない認証経路の再現テストでStateStore未指定の例外を確認し、KV無効のStateStoreを渡すよう修正した。
- Python 431件、Node 133件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、秘密ファイルを含めないコピーでの通常・E2E設定のWrangler dry-runが成功した。実サービスは未検証である。作成通知・実Cron・TTL超過は後続作業とする。


## 2026-09-12: 通常ポーリング経由を実サービスで検証

- [実行34615847619](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34615847619)で実装commit `9d54ccf3cd758e75c03c09b3e2a9ca2eb0afce6a` を専用Workerへ1回deployした。通常一覧取得から所有2件だけを選別し、上限1件・別HTTP残件消化・最終読戻しまで成功した。
- artifactの `batch_first_poll` / `batch_remaining_poll`、上限・残件・最終読戻し・KV回収の各200、Discord削除204・Notion archive 200を各2件、監査7操作を独立照合した。prepare / advance各1回、verify各段階1回で成功した。
- Worker version・run一致、outcome=passed、全資源dirty=false、JUnit 410件・失敗0を確認した。Actionsのローカル検査と専用E2E jobは成功した。
- KV読戻し再試行は発生していない。外部資源とKVの回収確認はAPI応答・delete完了の範囲である。Google反映・通知・通常の手動/Cron入口・TTL超過は今回の検証に含まない。


## 2026-09-12: 所有2件のE2Eを通常ポーリングへ接続

- upstream PR #66をマージし、fork同期PR #53をマージした。
- `discord_batch` の初回・advanceを通常ポーリング入口へ接続した。一覧取得後に所有2件のID・run marker・guild・初期内容と件数を検証し、固定順で差分処理へ渡す。
- 有効な他イベントの除外、逆順、初回・advanceそれぞれの欠落・重複・変更・不正要素・HTTP失敗をローカル検証した。拒否時のNotion・KV書込み禁止と回収を確認した。
- Python 410件、Node 124件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、秘密ファイルを含めないコピーでの通常・E2E設定のWrangler dry-runが成功した。実サービスでの通常ポーリング経由は未検証である。Google反映・通知・TTL超過は後続作業とする。


## 2026-09-12: 固定2件の上限・残件処理を実サービスで検証

- [実行34614558706](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34614558706)で実装commit `87460d282d399503636f42722164f5a0645b76f3` を専用Workerへ1回deployした。Discord event 2件を上限1件で適用し、別HTTPで残件1件を確認後、advanceで残りを適用・再読戻しした。
- prepare / advanceは各1回、verifyは各段階1回で成功した。2組のDiscord削除204・Notion archive 200と固定2キーの回収、outcome=passed、全資源dirty=falseをartifactで独立照合した。
- 監査とmanifestの7操作、run・Worker versionの一致、JUnit 399件・失敗0を照合した。Actionsのローカル検査と専用E2E jobは成功した。
- KV遅延の待機は発生していない。削除確認は外部API応答とKV delete完了の範囲で、全拠点の反映は保証しない。通常ポーリング・Google同期・通知・TTL超過・実保存失敗注入は後続作業である。


## 2026-09-12: 固定2件のKV残件処理を実装

- upstream PR #65とfork同期PR #52をマージした。
- `discord_batch` を追加し、固定2件を上限1件で通常差分処理へ渡す。別HTTPで残件1件を確認した後だけadvanceし、再度のHTTPでpage 2件・queue空を確認する。
- DOに2組の所有情報と回収完了を記録し、外部回収・KV固定2キー削除後だけcleanにする。古いKV再読込で処理済みの1件目を再適用しない。
- Python 399件、Node 124件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、秘密ファイルを含めないコピーでの通常・E2E設定のWrangler dry-runが成功した。実サービスは未検証である。
- 通常ポーリング・Google同期・通知・TTL超過は後続作業とする。追加対象外の項目10は変更しない。


## 2026-09-11: 外部fixtureと通常KVの接続を実サービスで検証

- commit `727a7008a2adfd0842c82eb1f9124acb3e0cf188` の[実行34605517604](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34605517604)が成功した。run `E2E-20260911T134041Z-a42a095d`、artifact `e2e-evidence-34605517604-1` を独立取得した。
- 専用Workerのdeploy 1回、所有Discord eventとNotion pageの準備1回、通常StateStoreのKV読戻し1回、cleanupと再cleanupを照合した。通常差分処理の適用、外部資源の所有権・内容の読戻し、Discord削除204、Notion archive 200、KV削除完了、outcome=passed、全資源dirty=falseを確認した。
- 監査とmanifestの操作順・成功応答、deploy時と最終Workerのversion fingerprint、run tagが一致した。ActionsのPython 376件、Node 115件、Ruff、Pyright、設定検査、Wrangler E2E dry-runが成功し、JUnit 376件・失敗0を独立照合した。
- 読戻し待機は発生していない。cleanupは削除・archive応答とKVのdelete完了を確認したもので、全拠点の削除反映は保証しない。複数イベント、通常ポーリング、残件、通知、TTL超過は後続作業である。

## 2026-09-11: 外部fixtureと通常KVの所有権を接続

- `discord_kv` を追加し、Discord event 1件・Notion page 1件・通常StateStoreの固定2キーを同じDO manifestで所有する。作成前にscopeを固定し、外部資源とKVの回収が完了してからcleanにする。snapshot / queueはDOへ複製しない。
- 通常差分処理での初回作成と、別HTTPでの外部資源・KV読戻しを接続した。Notionの適用時検索で既存page・検索失敗・不正応答を拒否する任意の制約を追加した。既定の通常同期は維持する。
- MCP・手動workflow・監査回収対象へ接続した。外部fixture作成は1回に限定し、未反映の固定応答だけを有限回待つ。
- Python 376件、Node 115件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査が成功した。秘密ファイルを含めない作業用コピーで通常・E2E両設定のWrangler dry-runも成功した。既存ユーザー変更2ファイルを保持した。
- この時点では実サービス未検証。複数件、通常ポーリング、残件、通知、TTL超過は後続作業である。

## 2026-09-11: 通常KV・DOの保存と別HTTP読戻しを実環境で検証

- commit `1e073cdc9e818fb7089417be96da3c0ba9a12599` の[実行34604249166](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34604249166)が成功した。run `E2E-20260911T132729Z-11bd50f1`、artifact `e2e-evidence-34604249166-1` を独立取得した。
- 専用Workerのdeploy 1回、通常StateStoreによる固定2キーの保存1回、別HTTP読戻し1回とHTTP 200を照合した。通常cleanupとworkflow末尾の再cleanupが成功し、`discord_state.outcome=passed`、全service / scenarioのdirty=falseを確認した。監査とmanifestの操作順・成功応答・deploy時と最終Workerのversion fingerprint・run tagが一致した。
- ActionsのPython 352件、Node 108件、Ruff、Pyright、設定検査、Wrangler E2E dry-runが成功した。JUnitも独立取得し352件・失敗0を照合した。
- 古いKV値による待機は発生しておらず、伝播遅延・全拠点の削除反映・ロックTTL超過はこの実行では検証していない。外部fixture・通常ポーリングは未接続であり、次に所有権の接続へ進む。

## 2026-09-11: 通常KV検証の手動workflow

- PR #64をupstream developへマージし、fork PR #51で同期した。
- 通常KV専用モードを追加した。保存は1回、未反映の固定応答だけを3秒間隔・最大25回まで読戻し、同run・version・検証済みmanifestを照合して所有2キーを回収する。検証失敗と回収後のfailed_cleanを成功にしない。
- Python 352件、Node 108件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査が成功した。既存ユーザー変更2ファイルは保持した。
- この時点では実KV・DOは未検証。外部fixtureと通常ポーリングは未接続である。

## 2026-09-11: 通常StateStoreのE2E隔離基盤

- run・scope別KVアダプターとDO所有権検証を追加した。通常StateStoreの固定2キーを、保存・別HTTP読戻し・回収の3経路で扱う。外部APIは呼ばず、snapshot / queueをDOへ複製しない。
- MCPの `discord_state` とcleanup対象を追加した。既存シナリオのpreflightを維持しつつ、新シナリオのdirty記録を共通statusへ含める。
- 項目10の5件をすべて追加対象外として `E2E-PLAN.md` に記録した。KV保存失敗・古い値・ロックTTL超過の検証は継続する。
- Python 352件、Node 93件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査が成功した。秘密ファイルを含めない作業用コピーで通常・E2E設定のWrangler dry-runが成功した。既存ユーザー変更2ファイルは保持した。
- 実KVの伝播・実サービス・通常ポーリングとの接続・手動workflowの専用モードは未検証・未接続である。

## 2026-09-11: 通常Discord同期の再試行消失と単独入口の競合を修正

- 修正前のローカル再現9件では、queue保存失敗後の作成・更新・削除と上限超過の残件消失、並行HTTPでの二重適用の5件が失敗した。
- 通常の保存順をqueue→snapshotへ変更し、手動・Cronの単独同期を全体同期と同じDOロックで保護した。結果保存までロックを保持し、例外・キャンセル時も解放する。競合・取得エラーでは同期と最終結果の更新を開始しない。
- 修正後のPython 319件、Node 89件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査が成功した。秘密ファイルを含めない作業用コピーで通常・E2E設定のWrangler dry-runも成功した。外部APIとKVを代替したローカル検証である。
- KVキーとDOの責務、ロックTTL、明示無効時の互換性を維持した。外部成功後の保存失敗では再実行され得る。実KVの伝播遅延、TTL超過、通常の全件適用・実Cronの実サービス検証は未実施。

## 2026-09-11: 応答本文破棄後の再送を実サービスで検証

- commit `effc0e3e3db13b829919d5cfcf50194e7ed86676` の[実行34597932061](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34597932061)が成功した。run `E2E-20260911T121617Z-de3d2c74`、artifact `e2e-evidence-34597932061-1` を独立取得した。
- 最初のadvanceの本文破棄flag・HTTP 200・固定エラーと本文由来statusがnullであること、2回のdeployの異なるversion ID、再deploy後のadvanceのupdated応答を照合した。
- 同時resumeの開始・開始・終了・終了の順序とHTTP 200 / 409、完了再送のalready_completed、全6回の差分・checkpoint、cleanup・全資源dirty=falseを確認した。意図した失敗記録は本文破棄1件とロック拒否1件だけだった。
- ActionsのPython 296件、Node 89件、Ruff、Pyright、設定検査、Wrangler dry-runが成功した。JUnitも独立取得し296件・失敗0を照合した。
- 実サービスを使ったMCP側の本文未読破棄試験であり、実際の回線断、Workerの途中停止、処理完了前の中断、DO claim自体の実環境競合は含まない。

## 2026-09-11: Discord差分E2Eの応答本文破棄と再送

- PR #61をupstream developへマージし、fork PR #48で同期した。ローカルdevelopとorigin/developの一致、upstream/developの祖先関係を確認した。
- 最初のadvanceはHTTP 200ヘッダー受信後にMCP側で本文を読まずcancelする。注入結果を固定flag・エラーで識別し、別のstatus取得によるcheckpoint照合、再deploy、advance再送、同時resumeへ続ける。本文を受け取っていない要求のdirtyはnullとして扱う。
- ローカルでNode 89件、Python 296件、Ruff、Pyrightが成功した。本文未読、cancelの実行、非200・通信失敗・cancel失敗の区別、入力範囲制限、監査flag、注入未確認時のcleanupを検証した。
- 実サービス検証はこの時点では未実施。MCP側の意図的な本文破棄を対象とし、実際の回線断、Workerの途中停止、処理完了前の中断は含まない。

## 2026-09-11: 同時resumeを実サービスで検証

- commit `526bcb0ce2cea9546237f45c0765ac6a378b1db3` の[実行34594293913](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34594293913)が成功した。run `E2E-20260911T113112Z-ae440f60`、artifact `e2e-evidence-34594293913-1` を独立取得して照合した。
- 2回のdeployの異なるversion IDと、再deploy後の各要求のversion指定を確認した。resumeの監査順は開始・開始・終了・終了・開始・終了で、並行要求のHTTP 200と409（e2e_lock_unavailable）、完了再送のalready_completedを確認した。期待した拒否以外は成功だった。
- 全6回の差分・checkpoint、状態分離・永続化、Discord削除204・読戻し404、Notion archive読戻し200、cleanupと全service / scenarioのdirty=falseを確認した。キャンセル後は一覧から消える分岐を観測した。
- ActionsのPython 296件、Node 76件、Ruff、Pyright、設定検査、Wrangler dry-runが成功した。JUnitを独立取得し、296件・失敗0も照合した。
- 今回の実サービス競合はHTTP入口の同期ロックによる拒否であり、DO claim自体の競合はローカル検証に限る。応答喪失、DOプロセスの強制再起動、任意位置のクラッシュ復旧は未検証。

## 2026-09-11: Discord差分E2Eの同時続行検証

- PR #60をupstream developへマージし、fork PR #47で同期した。ローカルdevelopとorigin/developの一致、upstream/developの祖先関係を確認した。
- 同run・同versionのresumeを2要求並行送信し、通常完了1件・HTTP入口ロック拒否1件を判定する。拒否を再送せず監査に保持し、両要求が終了するまでcleanupを開始しない。通常Workerの入口・ロック・DO claimの実装は変更していない。
- Python 296件、Node 76件、Ruff、Pyrightが成功した。HTTP入口の拒否側に外部API呼出しがないことと、入口を介さないDO claimの競合拒否を別々に確認した。
- 実サービスの同時要求はこの時点では未検証。DO claim競合の実環境再現、応答喪失、DOプロセスの強制再起動、任意位置のクラッシュ復旧は対象外とする。

## 2026-09-11: 再デプロイ後のDiscord差分続行を実サービスで検証

- commit `2232f7ab81626d74d803060cd9f490933cd72e3c` の[実行34593390627](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34593390627)が成功した。run `E2E-20260911T111946Z-80bb78e9` で専用Workerを2回deployした。
- artifact `e2e-evidence-34593390627-1` を独立取得し、2つのversion ID fingerprintが異なること、再deployのprevious値、最初の2要求と後続3要求のversion指定、最終Workerのversion・tag一致を確認した。更新後のadvance再送はupdated、完了後のresume再送はalready_completedだった。
- 全6回の差分・checkpoint処理、状態分離・永続化、削除204・読戻し404、Notion archive読戻し200、cleanup成功、全service / scenarioのdirty=falseを確認した。キャンセル後は一覧から消える分岐を観測した。
- ActionsのPython 294件、Node 67件、Ruff、Pyright、設定検査、Wrangler dry-runが成功した。JUnitも独立取得し、294件・失敗0を照合した。
- 実デプロイversionをまたぐ保存状態の継続を確認した結果であり、DOプロセスの強制再起動、応答喪失、同時実行競合、任意位置のクラッシュ復旧は含まない。

## 2026-09-11: Discord差分E2Eの再デプロイ継続検証

- 更新完了後に同run IDの専用Workerを再deployし、異なるversion IDを確認してから更新再送・続行・完了再送を行う。旧version、別run、未完了checkpoint、他資源dirtyでは再deployを拒否する。
- MCPの固定入力・監査にversion IDのSHA-256を追加し、Workerは要求されたtagとversion IDの不一致を外部操作前に拒否する。失敗時は同runの所有資源だけを回収する。
- Python 294件、Node 67件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査が成功した。実サービス検証はこの時点では未実施。DO bindingは変更せず、DOプロセスの強制再起動・任意位置のクラッシュ復旧はこの試験に含めない。

## 記録方針

- 目的、変更した文書または機能、実施した検証、未確認事項を簡潔に記録する。
- Git のコミット履歴を置き換えず、作業の判断と検証境界を補足する。
- シークレット、個人情報、外部サービスの認証値を記録しない。

## 2026-09-11: Discord差分E2Eの再送を実サービスで検証

- commit `17bf9489fc01b4cb8a1bc832ef4c34dca7e8bbc9` の[実行34589842665](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34589842665)が成功した。run `E2E-20260911T103519Z-dec00a51` でprepare、advance、advance再送、resume、resume再送の5リクエストを実行した。
- artifact `e2e-evidence-34589842665-1` を独立取得し、全経路のHTTP 200、更新後再送のupdated、完了後再送のalready_completed、全6回の差分・checkpoint、状態分離・永続化を確認した。再送は明示的に追加した要求であり、実際の応答喪失を起こした試験ではない。
- repository SHAとWorker version tagの一致、cleanup成功、outcome=passed、全サービスdirty=false、資源IDやsnapshot / queueを含めない証跡を確認した。キャンセル後は一覧から消える分岐を観測した。
- GitHub上のPython 284件、Node 55件、Ruff・Pyright・E2E設定検査・Wrangler dry-runが成功した。JUnitを独立取得し、284件・失敗0件も確認した。
- 実Worker再起動、同時実行競合、任意位置からの復旧、共有状態・全件適用・実Cronは未検証として保持する。

## 2026-09-11: Discord差分E2Eの明示再送検証

- 手動workflowを `prepare → advance → advance（再送）→ resume → resume（再送）` に拡張した。更新後の再送はupdated・dirty=true、完了後の再送はalready_completed・dirty=falseを必須にし、不一致時も同runだけを回収する。
- 監査JSONLとmanifestのoperationに固定列挙のexecution_statusを追加した。任意文字列や旧記録の欠落値はnullにし、応答本文は保存しない。
- Node 55件、Python 284件、Ruff、Pyright、E2E設定・Secret hygiene・workflow検査が成功した。再送のstatus・dirty・tool失敗で後続検証を止めてcleanupすること、実JSONL書込み・読戻しの固定値制限を確認した。
- この時点では明示再送の実サービス検証は未実施。実Worker再起動・応答喪失の発生・同時実行競合はこの変更の実サービス検証対象に含めない。

## 2026-09-11: Discord差分E2Eの更新後再開を実サービスで検証

- commit `2d8b73cc35ac41243a867108d01bc77c5a48821c` を対象に[実行34588410907](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34588410907)が成功した。run `E2E-20260911T101743Z-a9189d45` で、別HTTPの `prepare → advance → resume` を確認した。
- 更新・Notion読戻し後に保存した状態から続行し、全6回の差分・checkpoint処理、更新段階と続行段階の状態分離・永続化を確認した。キャンセル後は一覧から消える分岐を観測した。
- Discordの明示削除204・後続GET404、Notionのarchive読戻し200、cleanup成功、`outcome=passed`、`dirty=false` を確認した。
- artifact `e2e-evidence-34588410907-1` を独立取得し、3つのHTTP経路の順序と成功、repository SHAとWorker version tagの一致、全サービスのclean状態、資源ID・snapshot / queue・claim内部状態を出力しないことを照合した。
- GitHub上のPython 284件、Node 48件、Ruff・Pyright・E2E設定検査・Wrangler dry-runが成功した。JUnitを独立取得し、284件・失敗0件も確認した。
- 実Worker再起動、応答喪失による再送・競合、任意位置からの復旧、キャンセル後に一覧へ残る分岐、共有状態・全件適用・実Cronの実環境検証は含まない。再送・競合・オブジェクト再作成は以下のローカル検証に分けて保持する。

## 2026-09-11: Discord差分E2Eの更新完了境界からの続行

- `advance` を追加し、無変更・説明更新・Notion読戻し後に `delta_updated` とrevision 3を保存する。手動workflowは `prepare → advance → resume` で実行し、従来の2リクエスト経路と一括実行も維持する。
- 続行時は段階・revision・所有資源を再確認する。取得したrevisionをDOへ記録し、遅延した前段階の保存要求が次段階のclaimを解除することを防ぐ。保存完了後の再送は更新を繰り返さず、保存前中断はdirtyのままcleanupする。
- ローカル代替APIでPython 284件、Node 48件が成功した。新規経路の認証、version不一致の拒否、オブジェクト再作成、更新後の再送、古いclaim・資源変更の拒否、失敗後のcleanupを含む。
- Ruff、Pyright、E2E設定・Secret hygiene・workflow検査、Wrangler 4.127.1のE2E dry-runを確認した。dry-runには追跡対象のソースと設定だけを隔離コピーし、認証情報を渡していない。
- 今回は実サービスへの3リクエスト実行と実Worker再起動を検証していない。従来の2リクエスト実行の成功証拠は以下に保持する。共有状態、全件適用、実Cronは引き続き未確認。

## 2026-09-11: Discord差分E2Eの実サービス検証完了

- commit `7c1005958c003592f040332da6f905ee4509c0c4` を対象に[専用workflow実行34581609741](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34581609741)が成功した。`e2e` Environment承認後、専用Workerへdeployして実行した。
- run `E2E-20260911T085656Z-3563f478` で、別HTTPの `prepare → resume`、作成・無変更・更新・キャンセル・削除・削除後無変更を確認した。snapshot / queueの保存・復元も各stageが200で、通常共有状態は使用していない。
- 実環境ではキャンセル後に一覧から消える分岐を観測した。明示削除は204、後続GETは404、Notion pageはarchive読戻し200だった。一覧に残る分岐とWorker再起動は実環境では未検証。
- artifact `e2e-evidence-34581609741-1` を独立取得し、repository SHA・run ID・Worker version tagの一致、`outcome=passed`、`dirty=false`、cleanup成功、raw資源ID・snapshot / queueが記録されていないことを確認した。
- GitHub上のPython 266件、Node 47件、Ruff、Pyright、設定・Secret hygiene・workflow検査、Wrangler dry-runが成功した。JUnit XMLの266件・失敗0件も確認した。
- 初回失敗、旧runの回収、429でのfailed_cleanは下の履歴に分離して保持する。今回の成功で、それらを成功扱いへ変更しない。
- 共有状態・全Guild適用・Google反映・実Cronを含む全体E2Eは未完了であり、Issue #17は継続する。

## 2026-09-11: 旧run回収とDiscord一覧のレート制限対応

- [復旧実行34581033503](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34581033503)が成功し、初回runの `recovered`・`dirty=false` とWorker revisionの変更をartifactで確認した。
- [再実行34581260948](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34581260948)はDiscord一覧のHTTP 429で停止した。今回は `failed_clean`・`dirty=false` まで回収できた。初回失敗がUser-Agentだけに起因したとは断定しない。
- DiscordのGETに限り、応答 `retry_after` が有限・非負・10秒以内の場合に最大4回の試行を行う。副作用のある書込みはこの再試行の対象にしない。
- 最終artifactが旧version tagを返すケースも観測したため、Discord差分の各書込み入口でMCPが期待tagを送り、Workerが副作用前に検証する。不一致だけを最大20回・3秒間隔で待機する。
- Python 266件、Node 47件、Ruff、Pyrightでローカル検証した。

## 2026-09-11: Discord差分E2Eの初回実環境検証と復旧実装

- commit `3210352` をforkの `feature/e2e-discord-delta` へpushし、[実行34580597939](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34580597939)でローカル検査・deploy・version tag一致を確認した。
- Discord一覧取得で適用前に失敗した。Discord eventの削除は確認できたが、Notion作成試行の記録が先行していたため所有権未解決のdirtyが残った。
- 成功したfixture用HTTP経路との比較で、通常Discord API wrapperには公式形式のUser-Agentがないことを確認し追加した。初回artifactは一覧失敗の実HTTP statusを保持していないため、原因の断定は保留する。以後は固定範囲のstatusだけをstageへ残す。
- 一覧失敗が適用前であることを保存stageから確認できる場合に限定して未作成pageの回収を完了できるようにし、明示run ID専用の復旧モードを追加した。
- ローカルのPython 260件、Node 46件と静的検査で確認した。実環境の回収結果は後続記録へ残す。

## 2026-09-11: Discord差分E2EのHTTP間の準備・続行

### 変更

- `/admin/e2e/discord-delta-sync/prepare` で作成・読戻しまで実行し、保存済みsnapshot / queueと所有pageを `/resume` から読み直して続行する経路を追加した。
- 再開前にrun・対象fingerprint・保存時のイベント内容・Notion pageを確認し、DOで準備済みrunの続行を一度だけ取得する。古い管理記録から準備済み状態への巻戻しも拒否する。
- 成功済みの同runへの再送は外部操作を行わない。準備完了前や続行中の中断はcleanup対象とし、任意位置からは再実行しない。
- MCPの固定 `sync_phase=prepare/resume` と監査経路を追加し、Discord差分の手動workflowを2リクエストへ変更した。省略時の一括実行は維持した。

### ローカル検証

- Python単体テスト256件、MCP / workflow契約テスト45件、Ruff、Pyrightが成功した。
- 別HTTPリクエスト・Worker / DOオブジェクト再作成後の続行、成功後の再送、続行中断後のcleanup、prepare / resume各段階のworkflow失敗処理を確認した。
- E2E設定、Secret hygiene、workflow policy、PlantUMLモデル検査、固定Wrangler 4.127.1のE2E dry-runが成功した。
- dry-runは実認証情報とローカルSecretファイルを渡さない作業用コピーで実施した。Markdown相対リンク78件と `git diff --check` を確認した。

### 検証境界

- HTTP入口はローカルRequest、外部APIとDO storageは代替実装で検証した。実デプロイ、実サービス実行、実Worker再起動は未実施。
- 続行できるのは作成・読戻し完了後の準備境界であり、任意位置からの再開や外部API操作のexactly-once実行を保証するものではない。
- 通常同期の共有状態、全Guild適用、Google反映、実Cronは引き続き未確認。

## 2026-09-11: Discord差分E2Eのrun状態保存と復元

### 変更

- snapshot / queueの組を `discord_delta` manifest内へ一括保存する専用DO actionを追加した。
- 各差分処理の前に保存状態を読み直し、run・対象fingerprint・event ID・revisionが不一致なら後続適用を止める。
- fixtureの管理記録更新ではcheckpointを保持し、cleanup成功時のclean置換で消去する。dirty時もstatusへraw IDを公開しない。
- 更新・削除の失敗queueを、同じメモリstorageを引き継いでStateStore・DO・差分stateを作り直した後に再試行する回帰テストを追加した。

### ローカル検証

- Python単体テスト230件、MCP / workflow契約テスト42件、Ruff、Pyrightが成功した。
- E2E設定、Secret hygiene、workflow policy、PlantUMLモデル検査、固定Wrangler 4.127.1のE2E dry-runが成功した。
- dry-runは実認証情報とローカルSecretファイルを渡さない作業用コピーで実施した。
- 追跡対象Markdownの相対リンクと `git diff --check` を確認した。

### 検証境界

- 外部APIとDO storageを代替したローカル検証であり、デプロイ・実サービス実行・実Worker再起動は未実施。
- HTTPリクエストをまたぐscenarioの途中再開は未実装。中断runはdirtyを保持して所有資源をcleanupする。
- 通常同期の共有snapshot / queue、全Guild適用、Google反映、実Cronは引き続き未確認。

## 2026-09-11: Discord差分E2Eのキャンセル・削除検証

### 変更

- 既存の `discord_delta` scenarioへキャンセル、明示削除、削除後の変更なし判定を追加した。
- キャンセル後の一覧に残る場合は更新、消えた場合は削除として扱う通常処理を維持し、観測した分岐をstageへ記録する。
- 削除差分の前に個別GETの404と一覧消失を確認し、Notion再検索のpage ID一致を必須とした。
- Notionのarchiveをcleanup前に読み戻し、途中失敗を後続cleanupの成功で上書きしない。
- 更新・削除queueの再試行と、一覧から消えた完了eventを削除しない既存判定をローカル回帰テストで確認した。

### ローカル検証

- Python単体テスト209件、MCP / workflow契約テスト42件、Ruff、Pyrightが成功した。
- E2E設定、Secret hygiene、workflow policy、PlantUMLモデル検査が成功した。
- 実認証情報とローカルSecretファイルを渡さない作業用コピーで、固定Wrangler 4.127.1のE2E dry-runが成功した。
- 追跡対象Markdownの相対リンク78件と `git diff --check` が成功した。

### 検証境界

- 外部通信を代替したローカル検証であり、デプロイや実サービスのキャンセル・削除は未実施。
- キャンセル後の一覧の両応答形はローカルで確認し、実サービスで両分岐を観測済みとは扱わない。
- 共有snapshot / queue、全Guild適用、Google反映、実Cronは引き続き未確認。

## 2026-09-11: Discord差分同期の自己cleanup型E2E

### 変更

- 通常Discordポーリングから差分判定・適用・snapshot / queue更新を共通関数へ分離し、既存の通常動作を維持した。
- run所有event 1件を実一覧から選択して作成・無変更・説明更新を確認し、同じNotion pageへの反映を読み戻すscenarioを追加した。
- snapshot / queueは実行内stateへ限定し、作成通知先と共有state bindingを差分処理から隠した。
- E2E更新では既存page IDとの一致を必須にし、Notion再検索失敗を別pageの新規作成へ切り替えない。
- 独立した `discord_delta` manifest、専用route、MCP scenario、`deploy-and-discord-delta-smoke` 手動workflowモードを追加した。
- Discord→Notionの既存fixture / cleanupを再利用し、途中失敗、応答喪失、run ID・対象fingerprint不一致、dirty状態からの回収を検証した。

### 検証

- 外部APIを代替したPython単体テスト198件、MCP / workflow契約テスト42件、Ruff、Pyrightが成功した。
- E2E設定・Secret hygiene・workflow policy検査とPlantUMLモデル検査が成功した。
- 固定Wrangler 4.127.1のE2E dry-runは、Pythonソースと設定を作業用ディレクトリへ複製し、実認証情報とローカルSecretファイルを渡さず実施した。

### 未確認

- 新モードの実サービス実行とCloudflareへのデプロイ
- Discordの削除・キャンセル差分、共有snapshot / queueの永続化、全Guild適用、実Cron
- queue失敗・再試行の証拠はローカル代替によるものであり、実サービスの障害注入ではない。

## 2026-09-02: Google変更起因Webhook 自己cleanup型 E2E

### 目的

Google Calendarのevent更新で実際に送られる`exists`通知から、通常Workerと共通のWebhook ingressと同期dispatchへ進み、run所有event 1件だけをNotionへ反映・回収できることを確認する。

### 変更

- 専用Calendarにrun marker付きeventを作成し、600秒の短命watchへ届く初回`sync`を確認してからeventを更新する専用probeを追加した。
- callbackのchannel token、channel ID、resource ID、`exists`、message numberを検証し、Durable Objectで最初の通知だけを原子的にclaimする。watch作成応答より初回通知が先に届く場合も同じmanifestで解決する。
- 共通Webhook ingressと同期dispatchのGoogle差分取得結果からevent IDとrun markerが一致する1件だけを`apply_google_events`へ渡す。同期cursor、最終時刻、最終結果、Google認証cache、Notion対応表とqueueは実行内へ閉じ込める。
- cleanupはwatch停止とrun所有dedupe削除をevent削除より先に行う。停止に失敗した場合はGoogle eventとNotion pageを削除せずdirtyを維持し、明示cleanupで再試行する。
- MCPと手動GitHub Actionsへ`trigger_webhook_change`と`deploy-and-webhook-change-smoke`を追加した。

### ローカル検証

- `ruff check .`と`pyright`が成功した。
- Python単体テスト175件、MCP / workflow契約テスト40件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文、PlantUMLモデルが成功した。
- 固定Wrangler 4.127.1によるE2E Workerのdeploy dry-runが成功した。

### 反映

- 実装を[upstream PR #46](https://github.com/ichipiro/IE_Event_Bot/pull/46)と[fork同期PR #42](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/42)へ反映した。両PRは全チェック成功後にmerge commit方式で手動mergeした。
- `RELEASE_AUTOMATION_TOKEN`はorg側でPR作成、merge、fork同期、Environment承認に必要な権限が未付与である。このタスクでは付与済みと扱わず、認証済み対話セッションの`gh`で各操作を手動実行した。

### 実環境検証

- fork revision `6863ea9c5713b1dc754181807d166c9074d48885`の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33623500404)をrequired reviewer承認後に実行し、ローカルvalidation、専用Worker deploy、実通知scenario、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifact `e2e-evidence-33623500404-1`でrun ID `E2E-20260902T111449Z-e228731f`とWorker version tagの一致、repository clean、初回`sync`、event更新、実`exists`配信、最初の通知のclaim、Google差分取得、所有event 1件のNotion適用、実行内状態の分離を確認した。
- watch停止、run所有dedupe削除、Notion page archive、Google event削除と2回のcleanupはいずれも成功した。`webhook_change` manifestは`outcome=passed`、`dirty=false`である。
- 外部識別子は7件のSHA-256 fingerprintだけであり、固定schema検査と認証値・URL・生IDの混入検査も成功した。

### 未確認

- 通常Workerのwatch再登録・更新、共有cursorと全Calendarの全件適用、Discord反映、全体同期、実Cron配信
- 通常運用データを使うEnd-to-End検証

## 2026-09-02: Google Webhook初回実配信 自己cleanup型 E2E

### 目的

内部requestによるWebhook simulationとは別に、Google Calendarが作成直後のwatchへ送る初回`sync`通知が専用Workerの`/gcal/webhook`へ実際に到達することを、短命かつ自己cleanup型で確認する。

### 変更

- 外部request前にrun所有channel IDを強整合manifestへ記録し、固定HTTPS callback、channel token、有効期間600秒を指定して`events.watch`を呼ぶ専用probeを追加した。
- watch応答より初回通知が先に到達する場合と、応答後に到達する場合の両方をDurable Objectで原子的に解決する。channel ID、resource ID、`sync`、message number `1`が一致する通知だけを受理する。
- 初回通知の確認後は`channels.stop`でwatchを直ちに停止し、channel ID、resource ID、Calendar ID、callback URLはSHA-256 fingerprintだけをclean manifestへ残す。停止または所有権確認に失敗した場合はdirtyを維持する。
- 通常の同期dispatch、共有KV、`gcal_watch_state`、Google認証cache、Cronへ接続しない専用routeとして、MCPと手動GitHub Actionsへ`deploy-and-webhook-delivery-smoke`を追加した。

### ローカル検証

- `ruff check .`と`pyright`が成功した。
- Python単体テスト170件、MCP / workflow契約テスト38件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文、PlantUMLモデルが成功した。
- 固定Wrangler 4.127.1によるE2E Workerのdeploy dry-runが成功した。

### 反映

- 実装を[upstream PR #44](https://github.com/ichipiro/IE_Event_Bot/pull/44)と[fork同期PR #40](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/40)へ反映した。
- `RELEASE_AUTOMATION_TOKEN`はorg側で必要権限が未付与のため、付与済みとは扱わず、認証済み対話セッションの`gh`でPR作成、merge、fork同期、Environment承認を手動実行した。

### 実環境検証

- fork revision `8762928fc398b07085e615e550d54c5d0e4724da`の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33616522253)をrequired reviewer承認後に実行し、ローカルvalidation、専用Worker deploy、実配信scenario、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifact `e2e-evidence-33616522253-1`でrun ID `E2E-20260902T095359Z-f1a8a192`とWorker version tagの一致、repository clean、`watch_create=200`、`webhook_sync_delivery=204`、`watch_stop=204`を確認した。
- `webhook_delivery` manifestは`outcome=passed`、`dirty=false`、cleanupは`200`である。外部識別子は4件のSHA-256 fingerprintだけであり、固定schema検査と認証値・URL・生IDの混入検査も成功した。

### 未確認

- Google Calendar変更に起因する`exists`通知と、その通知から通常同期dispatchへ進む経路
- 通常Workerのwatch再登録・更新、共有cursor・対応表・queue、全体同期、実Cron配信

## 2026-09-02: Webhook ingress認証・重複抑止 E2E simulation

### 目的

Googleからの実配信を開始する前に、通常Workerと共通のWebhook ingress handlerでtoken認証とmessage重複抑止を自己cleanup型で実サービス検証できるようにする。

### 変更

- 通常の`/gcal/webhook`処理を共通handlerへ分離し、E2E専用routeから所有資源限定の内部requestを渡せるようにした。
- 誤ったchannel tokenが重複状態とdispatchを変更せず`401`になること、正しいtokenの1回目だけがdispatchされ、同じchannel IDとmessage numberの2回目が`204`で抑止されることを固定stageで確認する。
- 重複状態へE2E run IDを所有者として保存し、dirty manifest内のchannel ID、message number、run ID、fingerprintが一致する場合だけDurable Objectから削除する。削除失敗時はdirtyを維持し、`always()` cleanupで再試行する。
- Google event、Notion page、同期cursor、最終実行時刻、最終結果、Google認証cacheの既存の所有・分離境界は維持する。
- E2E Workerの通常`/gcal/webhook`とwatch作成は公開せず、Googleからの実配信と実Cronはこのsimulationの対象外とする。
- deploy時のrun IDをWorker version tagへ指定し、そのtagを専用Workerから読み戻した後だけscenarioを開始するrevision gateを追加した。

### ローカル検証

- `ruff check .`と`pyright`が成功した。
- Python単体テスト158件、MCP / workflow契約テスト36件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文、PlantUMLモデルが成功した。
- 固定Wrangler 4.127.1によるE2E Workerのdeploy dry-runが成功した。

### 実環境検証

- 1回目の[専用workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33612323486)はGitHub Actions上で成功したが、artifactのWorker version時刻とWebhook stageが更新前revisionのままであり、今回追加したtoken拒否・message重複抑止・重複状態fingerprintを証明しなかった。この実行は受入証拠に使用しない。更新前scenarioが作成したGoogle eventとNotion pageのcleanup、および`dirty=false`は確認した。
- 原因境界はWrangler processの正常終了後、専用Worker URLが新versionを返すことを確認せず直ちにscenarioを開始していた点である。Webhook ingress実装をupstream [PR #41](https://github.com/ichipiro/IE_Event_Bot/pull/41)とfork [PR #37](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/37)、run ID version tagのread-back gateをupstream [PR #42](https://github.com/ichipiro/IE_Event_Bot/pull/42)とfork [PR #38](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/38)へ反映した。
- 修正後の[専用workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33613460405)をrequired reviewer承認後にfork revision `3890ca60347c22101b0d63392fa5ea7750748bee`で実行した。artifact `e2e-evidence-33613460405-1`でrun IDとWorker version tagの一致、token不一致の`401`と副作用なし、初回・重複配信の`204`、message重複抑止、Google差分取得、所有eventだけのNotion適用、実行内状態の分離を確認した。
- 同artifactでGoogle event、Notion page、run所有の重複状態がすべてcleanupされ、`webhook_dispatch`が`outcome=passed`、`dirty=false`であることを確認した。`webhook_dispatch`の識別子は6件のSHA-256 fingerprintだけであり、schema検査と認証値・URL・生IDの混入検査も成功した。

### 未確認

- Googleから`/gcal/webhook`への実配信とwatch channel作成
- 通常同期の共有cursor、対応表、queue、全体同期、実Cron配信

## 2026-09-02: Webhook dispatch 自己cleanup型 E2E simulation

### 目的

通常のWebhook受信や共有同期状態を公開せず、所有・回収できるGoogle event 1件だけで差分取得から同期dispatchまでを実サービス検証できるようにする。

### 変更

- 専用Calendarのrun marker付きeventを、通常同期と共通のGoogle差分取得と`_run_sync_dispatch`へ通し、event IDとrun markerが一致する1件だけをNotionへ適用するprobeを追加した。
- Google eventとNotion pageを`webhook_dispatch`の強整合manifestで所有し、run IDと対象fingerprintの一致後だけcleanupする。
- 同期cursor、最終実行時刻、最終結果、Google認証cache、適用時の対応表とqueueを実行内へ閉じ込め、共有KVへ書き込まない。
- MCP `trigger_webhook`を専用simulation routeへ接続し、workflowに`deploy-and-webhook-simulation-smoke`と監査開始済みscenarioの`always()` cleanupを追加した。
- 通常の`/gcal/webhook`、watch channel、Webhook token、message-number重複抑止、全体同期、実Cronは既定拒否のまま維持した。

### ローカル検証

- `ruff check .`と`pyright`が成功した。
- Python単体テスト154件、MCP / workflow契約テスト34件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文、PlantUMLモデル、追跡対象Markdownの相対リンク検査が成功した。
- 固定WranglerによるE2E Workerのdeploy dry-runが成功した。

### 実環境検証

- [upstream PR #39](https://github.com/ichipiro/IE_Event_Bot/pull/39)と[fork同期PR #35](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/35)を全チェック成功後にmergeした。
- fork `develop`の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33609185862)をrequired reviewer承認付きで実行した。
- ローカルvalidation、専用Worker deploy、Google eventの作成・読取・差分取得、所有eventだけのNotion適用・読取、実行内状態の更新、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifactを独立に確認し、対象revision `7035da3`、repository clean、専用Webhook simulation routeのみの実行、2回のcleanup成功、`webhook_dispatch` manifest `passed`・clean、全16 stageがHTTP 200または204であることを確認した。
- artifactに生のWorker URL、認証情報、Google event ID、Notion page ID、Calendar ID、database IDがなく、外部資源はSHA-256 fingerprintだけであることを確認した。

### 未確認

- Googleから`/gcal/webhook`への実配信、watch channel、Webhook token、message-number重複抑止
- 通常同期の共有cursor、対応表、queue、全体同期、実Cron配信

## 2026-09-02: Notion期限cleanup 自己cleanup型 E2E scenario

### 目的

通常の内部 DB 全件処理と共有状態を公開せず、所有・回収できる2件だけで既存の期限判定と interval guard を実サービス検証できるようにする。

### 変更

- 期限判定、page archive、実行間隔判定を `_run_auto_clean_pages` へ分離し、通常ジョブからも同じ処理を呼ぶようにした。
- 専用内部 DB に作成する期限到来・将来日時 page を `notion_cleanup` の強整合 manifest で所有し、応答喪失時は page ごとに異なる run marker で再探索する自己 cleanup 型 probe を追加した。
- 最終実行時刻は probe 内へ閉じ込め、期限到来 page だけの archive と2回目の interval guard を確認する。共有 KV の `cleanup:last_epoch` は変更しない。
- MCP `trigger_job` の `cleanup` を通常の `/jobs/cleanup` ではなく所有資源限定 route へ接続し、workflow に `deploy-and-notion-cleanup-smoke` と監査開始済み scenario の `always()` cleanup を追加した。
- 通常のジョブ route、内部 DB 全件取得、共有 KV、実 Cron 配信は既定拒否のまま維持した。

### ローカル検証

- `ruff check .` と `pyright` が成功した。
- Python 単体テスト150件、MCP / workflow 契約テスト32件が成功した。
- E2E MCP 設定、Secret hygiene、workflow policy、Bash 構文、PlantUML モデル、追跡対象 Markdown の相対リンク検査が成功した。
- 固定 Wrangler による E2E Worker の deploy dry-run が成功した。

### 実環境検証

- [初回workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33602595302)は、HTTP 200の日時読戻しを文字列完全一致で検証した段階で失敗した。
- 初回失敗後もrun内cleanupと`always()` cleanupは成功し、artifactで `notion_cleanup` manifestが `failed_clean`、対象revision一致、repository clean、残存するdirty所有権なしであることを確認した。
- UTC offset と `Z` を同じ時刻として比較する修正後の[再実行workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33603187882)も同じ読戻し段階で失敗した。2回のcleanup、`failed_clean`、対象revision一致、repository cleanは再度確認した。
- 既存Notion CRUDとの入力差分だった秒精度をstubで再現し、fixture日時を分境界へ揃えた。不一致時は機密値を返さず項目名だけを固定エラーにする診断も追加した。
- [upstream PR #37](https://github.com/ichipiro/IE_Event_Bot/pull/37) と [fork 同期 PR #33](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/33) をmerge後、fork `develop` の[修正版workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33603941069)をrequired reviewer付きで実行した。
- ローカルvalidation、専用Worker deploy、期限到来・将来日時pageの作成と読取、期限到来pageだけのarchive、将来日時pageの維持、2回目のinterval guard、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifactを独立に確認し、対象revision `a6cca06`、repository clean、専用Notion cleanup routeのみの実行、2回のcleanup成功、`notion_cleanup` manifest `passed`・clean、全14 stageがHTTP 200、生のresource IDと認証情報の不在を確認した。

### 未確認

- 通常Notion cleanupジョブの内部DB全件取得、共有 KV の `cleanup:last_epoch`、実 Cron 配信
- Webhook simulation

## 2026-09-02: 前日リマインド 自己cleanup型 E2E scenario

### 目的

通常の Guild event 全件処理を公開せず、所有・回収できる1件だけで既存の前日リマインド判定と重複抑止を実サービス検証できるようにする。

### 変更

- 通知ウィンドウ判定、message 作成、通知済み cache 更新を `_run_reminder_events` へ分離し、通常ジョブからも同じ処理を呼ぶようにした。
- 通知ウィンドウ内の専用 Discord Scheduled Event と作成された message を `reminder` の強整合 manifest で所有し、応答喪失時は run marker で再探索する自己 cleanup 型 probe を追加した。
- 通知済み cache は probe 内へ閉じ込め、同じ event を2回処理して2回目の message が作成されないことを検証する。共有 KV の `reminder_cache` は変更しない。
- MCP `trigger_job` の `reminder` を通常の `/jobs/reminder` ではなく所有資源限定 route へ接続し、workflow に `deploy-and-reminder-smoke` と監査開始済み scenario の `always()` cleanup を追加した。
- 通常のジョブ route、Guild event 全件取得、共有 KV、実 Cron 配信は既定拒否のまま維持した。

### ローカル検証

- `ruff check .` と `pyright` が成功した。
- Python 単体テスト139件、MCP / workflow 契約テスト31件が成功した。
- E2E MCP 設定、Secret hygiene、workflow policy、Bash 構文、PlantUML モデル、追跡対象 Markdown の相対リンク検査が成功した。
- 固定 Wrangler による E2E Worker の deploy dry-run が成功した。

### 実環境検証

- [upstream PR #33](https://github.com/ichipiro/IE_Event_Bot/pull/33) と [fork 同期 PR #29](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/29) を merge 後、fork `develop` の[専用 E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33599347577)を required reviewer 承認付きで実行した。
- ローカル validation、専用 Worker deploy、Discord Scheduled Event の作成・読取、既存処理による Discord message 作成・読取、通知済み cache 更新、2回目の重複抑止、run 内 cleanup、`always()` cleanup、マスク済み evidence 収集が成功した。
- artifact を独立に確認し、対象 revision `597e1a1`、repository clean、専用 reminder route のみの実行、2回の cleanup 成功、`reminder` manifest clean、全16 stage 成功、raw resource ID と認証情報の不在を確認した。

### 未確認

- 通常リマインドジョブの Guild event 全件取得、共有 KV cache、実 Cron 配信
- Notion cleanup、Webhook simulation

## 2026-09-02: QA通知 自己cleanup型 E2E scenario

### 目的

通常のQ&A DB全件処理を公開せず、所有・回収できる1件だけで既存のQA通知判定を実サービス検証できるようにする。

### 変更

- 初回通知抑止、更新判定、未回答通知、cache更新を `_run_qa_notification_pages` へ分離し、通常ジョブからも同じ処理を呼ぶようにした。
- 専用Notion Q&A pageとDiscord messageを `qa_notification` の強整合manifestで所有し、応答喪失時はrun markerで再探索する自己cleanup型probeを追加した。
- 初回抑止用cacheはprobe内へ閉じ込め、page更新を読み戻した後に更新前markerを保持する。Notionの更新時刻が即時更新の前後で同値でも、共有KVを変更せずcache missを検証する。
- MCP `trigger_job` の `qa_check` を通常の `/jobs/qa-check` ではなく所有資源限定routeへ接続し、workflowに `deploy-and-qa-notification-smoke` と監査開始済みscenarioの `always()` cleanupを追加した。
- 通常のジョブroute、Q&A DB全件取得、質問番号補完、共有KVの `qa_cache` は既定拒否のまま維持した。

### ローカル検証

- `ruff check .` と `pyright` が成功した。
- Python単体テスト129件、MCP / workflow契約テスト30件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文、PlantUMLモデル・構文、追跡対象Markdownの相対リンク検査が成功した。
- 固定WranglerによるE2E Workerのdeploy dry-runが成功した。

### 実環境検証

- [upstream PR #31](https://github.com/ichipiro/IE_Event_Bot/pull/31) と [fork同期PR #27](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/27) をmerge後、fork `develop` の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33593477413)をrequired reviewer承認付きで実行した。
- ローカルvalidation、専用Worker deploy、Notion Q&A pageの作成・読取・更新、初回通知抑止、既存処理によるDiscord message作成・読取、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifactを独立に確認し、対象revision `2d956cc`、repository clean、専用QA通知routeのみの実行、2回のcleanup成功、`qa_notification` manifest clean、全20 stage成功、raw resource IDと認証情報の不在を確認した。

### 未確認

- 通常QAジョブのQ&A DB全件取得、質問番号補完、共有KV cache、実Cron配信
- 前日リマインド、Notion cleanup、Webhook simulation

## 2026-09-02: Discord→Google 自己cleanup型 E2E scenario

### 目的

通常の全体同期を公開せず、所有・回収できる最小範囲で既存の Discord→Google 適用処理を実サービス検証できるようにする。

### 変更

- 専用 Discord Scheduled Event を作成・読取し、既存の `_sync_discord_event_upsert` で専用 Google Calendar の event へ反映して内容を確認する E2E scenario を追加した。
- Discord Scheduled Event と Google event を `discord_google` の強整合 manifest で所有し、片方でも cleanup または所有権確認に失敗した場合は dirty を維持する。
- 通常設定の Google 同期を無効のまま維持し、1件の適用呼び出しだけを有効化する env view から内部・外部 Notion DB を隠した。通常の Discord snapshot / queue と作成通知は変更しない。
- MCP `trigger_sync` に固定 `discord_google` scenario を追加し、workflow に `deploy-and-discord-google-smoke` と監査開始済み scenario の `always()` cleanup を追加した。
- 通常同期、Webhook simulation、ジョブの既定拒否は維持した。

### ローカル検証

- `ruff check .` と `pyright` が成功した。
- Python 単体テスト118件、MCP / workflow契約テスト28件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文、PlantUMLモデル検査が成功した。
- 固定 Wrangler による E2E Worker の deploy dry-run が成功した。

### 実環境検証

- [upstream PR #29](https://github.com/ichipiro/IE_Event_Bot/pull/29) と [fork同期PR #25](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/25) をmerge後、fork `develop` の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33591103445)をrequired reviewer承認付きで実行した。
- ローカルvalidation、専用Worker deploy、Discord Scheduled Event作成・読取、既存適用処理によるGoogle event作成・検証、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifactを独立に確認し、対象revision一致、repository clean、固定scenario routeのみの実行、2回のcleanup成功、`discord_google` manifest clean、全13 stage成功、raw resource IDと認証情報の不在を確認した。

### 未確認

- Discord 一覧差分、snapshot / queue、更新・削除経路、作成通知
- Google 差分取得、同期 cursor / queue、Notion 反映
- 全体同期、実 webhook、Cron、定期ジョブ

## 2026-09-02: Discord→Notion 自己cleanup型 E2E scenario

### 目的

通常の全体同期を公開せず、所有・回収できる最小範囲で既存の Discord→Notion 適用処理を実サービス検証できるようにする。

### 変更

- 専用 Discord Scheduled Event を作成・読取し、既存の `_sync_discord_event_upsert` で専用 Notion 内部 DB の page へ反映して内容を確認する E2E scenario を追加した。
- Discord Scheduled Event と Notion page を `discord_notion` の強整合 manifest で所有し、片方でも cleanup または所有権確認に失敗した場合は dirty を維持する。
- Google 同期、外部 Notion DB、Notion プロパティ名上書きを事前拒否し、通常の Discord snapshot / queue と作成通知を変更しない境界を追加した。
- MCP `trigger_sync` に固定 `discord_notion` scenario を追加し、workflow に `deploy-and-discord-notion-smoke` と監査開始済み scenario の `always()` cleanup を追加した。
- 通常同期、Webhook simulation、ジョブの既定拒否は維持した。

### ローカル検証

- `ruff check .` と `pyright` が成功した。
- Python 単体テスト109件、MCP / workflow契約テスト27件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文、PlantUMLモデル検査が成功した。
- 固定 Wrangler による E2E Worker の deploy dry-run が成功した。

### 実環境検証

- [upstream PR #27](https://github.com/ichipiro/IE_Event_Bot/pull/27) と [fork同期PR #23](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/23) をmerge後、fork `develop` の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33586744127)をrequired reviewer承認付きで実行した。
- ローカルvalidation、専用Worker deploy、Discord Scheduled Event作成・読取、既存適用処理によるNotion page作成・検証、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifactを独立に確認し、対象revision一致、repository clean、固定scenario routeのみの実行、2回のcleanup成功、`discord_notion` manifest clean、全必須stage成功、raw resource IDと認証情報の不在を確認した。

### 未確認

- Discord 一覧差分、snapshot / queue、更新・削除経路、作成通知
- Discord→Google、全体同期、実 webhook、Cron、定期ジョブ

## 2026-09-02: Google→Discord 自己cleanup型 E2E scenario

### 目的

通常の全体同期を公開せず、所有・回収できる最小範囲で既存の Google→Discord 適用処理を実サービス検証できるようにする。

### 変更

- 専用 Google event を作成・読取し、既存の `_sync_to_discord` で専用 Discord Guild の Scheduled Event へ反映して内容を確認する E2E scenario を追加した。
- Google event と Discord Scheduled Event を `google_discord` の強整合 manifest で所有し、片方でも cleanup または所有権確認に失敗した場合は dirty を維持する。
- 通常設定の `DISCORD_SYNC_ENABLED=false` を維持し、専用 event 1件の適用呼び出しだけを一時的に有効化する境界を追加した。Notion、通常 KV の同期対応表、queue は変更しない。
- MCP `trigger_sync` に固定 `scenario` 列挙を追加し、workflow に `deploy-and-google-discord-smoke` と監査開始済み scenario の `always()` cleanup を追加した。
- 通常同期、Webhook simulation、ジョブの既定拒否は維持した。

### ローカル検証

- `ruff check .` と `pyright` が成功した。
- Python 単体テスト100件、MCP / workflow契約テスト26件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文検査が成功した。
- 固定 Wrangler による E2E Worker の deploy dry-run が成功した。

### 実環境検証

- [upstream PR #25](https://github.com/ichipiro/IE_Event_Bot/pull/25) と [fork同期PR #21](https://github.com/lycanthr0pes/IE_Event_Bot_fork/pull/21) をmerge後、fork `develop` の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33582230579)をrequired reviewer承認付きで実行した。
- ローカルvalidation、専用Worker deploy、Google event作成・読取、既存適用処理によるDiscord Scheduled Event作成・検証、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifactを独立に確認し、対象revision一致、repository clean、固定scenario routeのみの実行、2回のcleanup成功、`google_discord` manifest clean、全必須stage成功、raw resource ID不在を確認した。

### 未確認

- Google 差分取得、同期 cursor / queue、全体同期
- Notion 反映、実 Google webhook、Cron、定期ジョブ

## 2026-09-02: Google→Notion 自己cleanup型 E2E scenario

### 目的

通常の全体同期を公開せず、所有・回収できる最小範囲で既存の Google→Notion 適用処理を実サービス検証できるようにする。

### 変更

- 専用 Google event を作成・読取し、`apply_google_events` で専用 Notion 内部 DB へ反映して内容を確認する E2E scenario を追加した。
- Google event と Notion page を `google_notion` の強整合 manifest で所有し、片方でも cleanup または所有権確認に失敗した場合は dirty を維持する。
- 外部 Notion DB、Discord 反映、Notion プロパティ名上書きを事前拒否し、同期対応表と queue を永続化しない境界を追加した。
- MCP `trigger_sync` を通常の `/sync/all` から専用 scenario route へ変更し、workflow に `deploy-and-google-notion-smoke` を追加した。
- 通常同期、Webhook simulation、ジョブの既定拒否は維持した。

### ローカル検証

- `ruff check .` と `pyright` が成功した。
- Python 単体テスト92件、MCP / workflow契約テスト25件が成功した。
- E2E MCP設定、Secret hygiene、workflow policy、Bash構文検査が成功した。
- 固定 Wrangler による E2E Worker の deploy dry-run が成功した。

### 実環境検証

- upstream PR #23とfork同期PR #19をmerge後、fork `develop` の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33579456642)をrequired reviewer承認付きで実行した。
- ローカルvalidation、専用Worker deploy、Google event作成・読取、既存適用処理によるNotion page作成・検証、run内cleanup、`always()` cleanup、マスク済みevidence収集が成功した。
- artifactを独立に確認し、対象revision一致、repository clean、全操作成功、`google_notion` manifest clean、必須stage成功、raw resource ID不在を確認した。

### 未確認

- Google 差分取得、同期 cursor / queue、Discord 反映
- 実 Google webhook、Cron、定期ジョブ

## 2026-09-02: 未所有 E2E orchestration の既定拒否

### 目的

同期、Webhook simulation、定期ジョブが変更する下流資源を自己 cleanup できるまで、専用 E2E Worker から誤実行できない状態にする。

### 変更

- `E2E_ORCHESTRATED_WRITES_ENABLED` を追加し、既定値と専用 Wrangler 設定を `false` にした。
- 無効時は同期、Webhook simulation、ジョブ route を認証情報や run ID の有無にかかわらず `404` で隠す。
- status と MCP preflight に既定拒否の確認を追加した。
- 自己 cleanup 型へ移行する残作業を GitHub Issue #17 と `docs/ISSUES.md` に記録した。

### 検証

- 無効時の route 非委譲と、有効時の既存認可・run ID 境界をローカル単体テストで確認した。
- MCP preflight が無効状態を成功、明示的な有効状態を失敗と判定する契約テストを追加した。
- `ruff check .`、`pyright`、Python 単体テスト85件、MCP 契約テスト23件が成功した。
- E2E 設定検査、Secret hygiene、workflow policy、Wrangler dry-run、追跡対象 Markdown の相対リンク検査が成功した。

### 未確認

- サービス間同期、Google webhook、Cron の実配信
- 同期・通知・cleanup ジョブが作る下流資源の自己 cleanup

## 2026-09-02: クリーンなチェックアウトの文書リンク修正

### 目的

追跡対象外のローカル補助文書を相対リンクとして扱わず、クリーンなチェックアウトで追跡対象 Markdown のリンクが解決する状態にする。

### 変更

- `AGENTS.md` の4つのローカル補助文書を、Markdown リンクからパス表記へ変更。
- 4文書を追跡対象外で維持し、標準文書を正本とする既存方針は変更しない。

### 検証

- Git 追跡対象の Markdown から参照する相対リンクがすべて追跡対象ファイルへ解決することを確認。
- `ruff check .`、`pyright`、`pytest -q`、MCP 契約テスト、E2E 設定検査、Wrangler dry-run が成功。
- `git diff --check` が成功。

## 2026-08-29: 5件の課題解決

### 目的

`docs/ISSUES.md` に記録された5件を解決し、ローカル検証と実環境確認の境界を明確にする。

### 変更

- 外部通信を遮断した単体テスト基盤を追加し、CI で `pytest -q` を常時実行。
- `INTERNAL_API_TOKEN` 未設定時も同期、管理、ジョブ API を拒否する fail-closed へ変更。
- `GCAL_WEBHOOK_TOKEN` を Google watch の channel token として登録・照合し、旧 watch と token 変更時の再登録を追加。
- Google watch API の外部エラー本文を管理応答や状態履歴へ流さず、Secret の反射を防止。
- `package.json` と `package-lock.json` で Wrangler `4.127.1` を固定。
- 4つの詳細文書は削除・追跡追加・本文変更をせず、追跡対象外のローカル補助として維持。

### 検証

- Python 依存の基本インポートが成功。
- `.venv/bin/ruff check .` が成功。
- `.venv/bin/pyright` がエラー0件、警告0件で成功。
- `.venv/bin/pytest -q` が26件成功。
- `npm ci --ignore-scripts` が成功し、npm の依存監査は既知の脆弱性0件。
- 固定 Wrangler の版確認と `deploy --dry-run --config workers/wrangler.jsonc` が成功。
- `git diff --check` が成功。

### 未確認

- Cloudflare 上の `INTERNAL_API_TOKEN` と `GCAL_WEBHOOK_TOKEN` の登録状態
- token 付き Google watch の再登録と Webhook の実配信
- Cloudflare WAF、レート制限、Workers KV、Durable Objects の実ランタイム動作
- Discord、Google、Notion の実 API 疎通と権限

## 2026-08-29: ローカル単体テスト基盤の追加

### 目的

Cloudflare や外部 API へ接続せず、同期制御と状態管理の主要な回帰を Linux / WSL で検出できるようにする。

### 変更

- Cloudflare Workers の Response、Worker、Durable Object と、KV / DO storage のローカル代替を追加。
- 意図しない外部通信を即時失敗にする既定の `fetch` を追加。
- 認可、クールダウン、ロック、Webhook 重複、Google / Discord キュー繰り越しのテストを追加。
- CI のテスト検出・スキップを廃止し、`pytest -q` を常時実行するように変更。
- `docs/TESTING.md` に実行方法と検証境界を記載。

### 検証

- `.venv/bin/pytest -q` が成功。
- `.venv/bin/ruff check .` が成功。
- `.venv/bin/pyright` が成功。
- `git diff --check` と Markdown 相対リンク検査が成功。

### 未確認

- Cloudflare Python Workers、Workers KV、Durable Objects の実ランタイム動作
- Discord、Google、Notion の実 API 疎通と権限
- Cron、Google watch、Webhook の実配信

## 2026-08-29: エージェント指示と文書構成の標準化

### 目的

`agents-setup` テンプレートを基礎に、既存の `AGENTS.md` と文書を失わず、日本語の標準目次へ統合する。

### 変更

- `AGENTS.md` を日本語化し、既存の WSL、`.venv`、Cloudflare Workers、検証ルールを統合。
- `docs/DEVELOPMENT.md` を追加し、テンプレート規則と Conventional Commits の既存運用を統合。
- 標準目次の要件、フロントエンド、バックエンド、セキュリティ、データ設計、参照、課題、目標、作業履歴、文書変更履歴を追加。
- 既存の仕様、KV、運用、Durable Object / KV、Fork / Upstream 文書を統合元として保持。
- 英語本文だった `docs/do-kv-design.md` を日本語化。
- `docs/Operations.md` と `docs/KV.md` の PowerShell 例を WSL / Linux 向けの Bash 例へ統合。

### 検証

- `AGENTS.md` の相対 Markdown リンク19件が、すべてリポジトリ内の実在ファイルへ解決することを確認。
- テンプレート目次が要求する12文書がすべて存在することを確認。
- 依存関係の基本インポートが成功。
- `ruff check .` が成功。
- `pyright` がエラー0件、警告0件で成功。
- `git diff --check` と新規文書の末尾空白検査が成功。
- テストファイルが存在しないため、CI の方針に合わせて pytest は実行対象外とした。

### 未確認

- Cloudflare、Discord、Google、Notion の実環境動作
- GitHub 側の Actions、Secret、ruleset、branch protection の現在状態
