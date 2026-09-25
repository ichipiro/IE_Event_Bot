# 文書変更履歴

## 2026-09-25

- NotionへのDiscord ID書戻し拒否後の復旧E2Eを追加し、[実行36147796164](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36147796164)の成功、部分反映IDの再利用、queue復旧・重複なし・全資源回収の独立照合を[書戻し復旧の検証記録](E2E-NOTION-WRITEBACK-RETRY.md)へ保存した。

- Notionページ作成拒否後の復旧E2Eを追加し、[実行36145925999](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36145925999)の成功、queue維持・再試行・重複なし・全資源回収の独立照合を[検証記録](E2E-NOTION-CREATE-RETRY.md)へ保存した。

- 通常Notion cleanupの全件取得・共有KV・別HTTPでの実行間隔抑止・所有資源回収を実サービスで確認（[実行36038438985](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36038438985)）。計画を完了へ更新し、実Cronと失敗後再試行は別の未検証範囲として残した。

- 通常リマインドの全件取得・対象選別・共有cache・重複抑止・回収を実サービスで確認（[実行36033540656](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36033540656)）。一覧取得失敗の成功誤判定とGETの429待機を修正した。

## 2026-09-24: 通常HTTP・共有KVの全体同期E2E

`deploy-and-all-http-smoke` の入口、4段階、共有KVとDOの責務、回収範囲をTESTING・SECURITYへ追記。従来9段階の隔離全体同期と区別する。初回の回収済み失敗と、既知削除履歴の対応表検証を修正した実行36010723441の成功・回収証跡を記録。

## 2026-09-24: 全体同期9段階の実サービス検証成功

- TESTING、E2E-PLAN、ISSUES、WORKLOGへ全9段階・回収成功、監査・version照合と、run別KV・固定失敗・同一HTTP内競合という検証範囲を記録した。

## 2026-09-24: 全体同期E2Eの範囲と残作業

- TESTING、E2E-PLAN、ISSUES、WORKLOGへ所有2件・9段階の全体同期、回収条件、ローカル検証と実サービス未実行の境界を記録した。

## 2026-09-24: Google matrix 18段階の成功を記録

- 実行35982356318の全18段階、3件のAPI拒否・分割再試行、既存ID維持、全資源・共有KV回収とpassed・dirty=falseを記録し、計画の対応項目を完了にした。最初のロック解放失敗の原因未確定という境界は維持する。

## 2026-09-24: Google matrixの回収成功を記録

- 回収実行35981499346の所有資源・共有KV回収、failed_clean・全記録dirty=falseを記録した。元の実行の失敗と再検証待ちを維持する。

## 2026-09-24: Google matrixの複数失敗・再試行範囲を追加

- 複数日の終日・UTC日跨ぎ予定、残存3件のAPI拒否、上限1件のqueue再試行を18stepとして文書化した。ローカル件数別12組の検証と実サービス未確認の境界を追記した。

## 2026-09-24: Google matrix E2Eの成功を記録

- [実行35977892750](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35977892750)の全14段階、共有queue再試行、繰返し親・共有KVの回収、passed・全記録dirty=falseを記録し、対応するE2E計画項目を完了にした。

## 2026-09-24: Google matrix E2E

- 予定形式と5件・複数回繰越・共有KV再試行のmatrixモード、14stepのverify・所有記録・繰返し親の回収、MCP・workflowと検証境界を文書化した。

## 2026-09-24: Google全件E2Eの成功を記録

- 実行35969469480の4段階・既存削除履歴の保護・共有KV回収、passed・全記録dirty=falseを記録し、対応するE2E計画項目を完了にした。

## 2026-09-24: 全件E2Eの処理時間と呼出し待機時間を調整

- 実行35968760516の削除履歴保護成功・50秒タイムアウト・全資源回収を記録した。全件phase90秒、Google同期HTTP120秒、workflowのMCP180秒を揃えた。

## 2026-09-24: 削除履歴のあるCalendarでの全件E2E

- 既存の削除履歴を開始時に記録し、一致する履歴だけを保護して除外する方式へ変更した。新規Calendarへの切替は必須ではなくなり、通常予定と未知・変更された履歴の拒否を維持する。

## 2026-09-24: Calendar診断結果

- 実行35965137690の成功、削除履歴のみの残存、既存manifestの不変、全件E2E再開に必要なCalendar条件を記録した。

## 2026-09-24: Calendar診断経路

- 全ページのstatusだけを確認する専用route・MCP phase・手動workflowと、診断を回収対象へ含めない処理を追加した。

## 2026-09-24: 新規KVでの全件E2Eの結果

- 実行35963510311のKV検査通過、Calendar開始条件での停止、エラー分類の限界、前回clean記録の保持を文書化した。

## 2026-09-24: 全件E2Eの開始条件拒否を記録

- 実行35962599536のdeploy成功、共有KV既存値による適用前停止、前回clean記録の保持、再実行条件をE2E計画・検証資料・課題・作業履歴へ記録した。

## 2026-09-24: Google共有KV・全件モード

- 専用環境の全取得入力を共有KVへ適用する4段階モード、開始時の空状態検査、書込み予定の所有記録と回収、MCP・手動workflowを追加した。
- 削除記録を含むCalendarの開始拒否、最終時刻のKV fallback、実サービス未検証の境界をTESTING・E2E計画・課題へ記録した。

この文書は、標準文書構成の変更を記録する。アプリケーションの公開リリース履歴は、Release Please が管理するリポジトリルートの `CHANGELOG.md` を正本とする。

## 2026-09-24

- [再実行35959152201](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35959152201)で不正日時へのAPI拒否・queue回復・全6段階と回収が成功した結果を記録した。実APIの入力検証エラーとサービス障害を区別し、残作業を更新した。

- [復旧実行35955045460](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/35955045460)の成功、空名になった所有Discord予定を含む回収、`failed_clean`・全資源clean、run/version/commitとartifactの照合結果を記録した。

- API拒否E2E初回の失敗・dirty資源、空名が受理されたモデルの再現、限定的な所有確認と回収専用workflow、不正日時への試験変更を記録した。

- 通常Google同期のHTTP失敗・subrequest上限による残件消失と成功誤判定の修正、ローカル再現結果を記録した。所有Discord予定の不正PATCHに対するAPI拒否とqueue再試行を専用E2Eへ追加し、実サービス障害・共有状態の全件適用とは分けた。

## 2026-09-15

- Google同期の固定部分失敗・queue再試行を[実行34866761198](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34866761198)で検証した。6段階・回収が成功し、監査・manifest・version・commitの照合結果を記録した。

- Google→Discord作成・更新失敗の残件保存修正と、固定部分失敗・queue再試行の6段階E2Eを記録した。実障害観測と固定注入の境界を明記した。

- 修正後の[通常Google同期E2E](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34862331643)で所有2件の繰越・消化・更新・削除、別HTTP読戻し、全資源回収が成功した。監査・manifest・version・commitを照合し、実サービスで確認した範囲を計画と検証文書へ反映した。
- 通常Google同期E2Eの初回prepare失敗・全資源回収と、KV欠損値のローカル再現・修正を記録した。
- 通常Google同期E2Eの所有2件・run別KV・DO段階管理・繰越消化・更新・削除・別HTTP読戻し・回収、MCPと手動workflowを追加した。ローカル検証と実サービス未実施を区別した。

## 2026-09-14

- 状態障害の保証範囲表と限界を再現する8ケース、通常Google同期のローカル接続3ケースを追加した。E2E計画項目3の境界確定を完了にし、項目4のローカル確認と実サービス未検証を分けた。

- PlantUMLのpush・Pull Request自動起動を削除し、生成・検証ジョブを手動実行のみにした。

- 分割した状態障害E2Eの実KV・DO成功、8ケース・読戻し・回収、version一致、`passed`・全資源cleanの証拠を反映した。修正版は実行時点で未マージ。

- 状態障害E2Eの実環境失敗と回収結果を記録し、8ケースを1 HTTPずつに分割する対策、途中失敗の再実行拒否、timeoutの明示分類を反映した。

- 古いqueueによる残件喪失に対し、snapshot内の `_pending_sync` から作成・更新・削除・通知待ちを復元する対策を記録した。E2Eの埋込残件の所有権検査と、旧形式・両キーが古い場合の保証境界を更新した。

- `sync_faults` の固定KV障害7ケース・TTL超過・所有結果読戻し・回収、旧ownerの結果保存拒否を記録した。ローカル検証と実環境未実施、古いqueueによる残件喪失と重複適用の未解決範囲を区別した。

- [実行34834547224](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34834547224)で通常同期の共通ロック競合6 round、実KVの結果読戻し・回収、version一致を確認し、E2E計画と検証結果を更新した。

- 通常同期の共通ロックを使う `sync_lock` E2E、結果KVの所有・回収、専用workflowと検証境界を記録した。ローカル検証済みと、実KV・DO・実Cronの未検証範囲を区別した。

- [実行34831533775](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34831533775)の通知2件・同一messageへのリアクション再試行・全所有資源回収・version一致を独立照合し、E2E計画、検証文書、課題、作業履歴へ記録した。

- `discord_batch_notification` の所有message記録・別HTTP再試行・回収、専用workflow、KVとDOの責務を記録した。ローカル検証と実サービス未検証を分け、E2E計画を更新した。

## 2026-09-12

- 通常Discord同期の作成通知繰越・失敗再試行、投稿済みmessageのリアクション再試行、queue互換と重複配信の保証境界を記録した。通知22件のローカル検証が成功し、実サービス接続は後続作業として区別した。

- 通常ポーリングから所有2件をGoogle・Notionへ反映する `discord_batch_google` を追加した。固定ID・対応ID・作成着手記録・部分失敗回収と、専用手動workflowをローカル検証した。[実行34619150601](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34619150601)で3サービス各2件の適用・残件・読戻し・全資源回収を実サービス確認した。

- 固定2件のE2Eを通常ポーリング入口へ接続し、一覧取得後の所有確認・選別・並び順固定と、異常一覧での書込み拒否をローカル検証した。[実行34615847619](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34615847619)で初回・残件の通常ポーリング、Notion反映、KV読戻し、全資源回収を実サービス確認した。

- 固定2件の所有・上限1件・通常KV残件の別HTTP消化・回収を行う `discord_batch` と専用手動workflowを追加した。ローカル検証に加え、[実行34614558706](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/34614558706)の上限・残件処理・全資源回収・version一致を記録した。

## 2026-09-11

- 外部fixtureと通常KVの接続の実サービス成功（実行34605517604）、削除・archive応答、KV回収、artifactの独立照合を記録した。

- 外部fixtureと通常KVを同じ所有記録へ接続するシナリオ、MCP・手動workflow、回収と検証境界を記録した。

- 通常KV・DOの保存、別HTTP読戻し、所有キー回収の実環境成功（実行34604249166）とartifactの独立照合を記録した。

- 通常KV専用の手動workflow、有限回の読戻し、revision照合、失敗時の回収とローカル検証結果を記録した。

- 通常StateStoreのKV隔離・所有権・別HTTP検証を記録し、`E2E-PLAN.md` に残作業とユーザー指定の追加対象外5件を整理した。

- 通常Discord同期のqueue保存失敗による再試行消失と手動・Cronの排他漏れの修正、ローカル回帰結果、残る実KV・TTLの検証境界を記録した。

- 応答本文破棄後の再送の実サービス成功（実行34597932061）、破棄flag・再送応答・checkpoint・cleanupの独立照合を記録した。

- Discord差分E2EにMCP側の応答本文未読破棄、checkpoint確認後の再送、障害注入の監査と検証範囲を追加した。

- 同時resumeの実サービス成功（実行34594293913）、並行開始・HTTP 200 / 409・checkpoint・cleanupの独立照合を記録した。

- Discord差分E2Eに同時resumeの通常完了・ロック拒否の判定と、両要求終了後のcleanupを追加し、入口ロックとDO claimの検証範囲を区別した。

- 再デプロイ後のDiscord差分続行の実サービス成功（実行34593390627）と、2つのversion ID・checkpoint・cleanupの独立照合を記録した。

- Discord差分E2Eに更新完了後の再deploy、異なるversion IDへの続行、version fingerprintの監査と検証境界を追加した。

- Discord差分E2Eの明示再送を含む実サービス成功（実行34589842665）と独立したartifact照合を記録した。

- Discord差分E2Eに更新後・完了後の明示再送と、固定応答statusの監査記録を追加した。

- Discord差分E2Eの3リクエスト構成による実サービス成功（実行34588410907）と独立したartifact照合結果を追記した。

- Discord差分E2Eに更新完了後の再開境界を追加し、3リクエストでの実行、claimの検証、ローカル検証と従来の実サービス証拠の範囲を記録した。

- Discord差分E2Eの実サービス成功、初回失敗と旧run回収、429対応、version確認の証拠を記録し、Issueの確認済み範囲を更新した。

- Discord差分E2Eの準備・続行を別HTTPリクエストへ分割し、MCPの固定 `sync_phase`、単一claim、再送時の挙動と中断時のcleanup境界を記録した。

- Discord差分E2Eのrun単位checkpoint保存・復元・cleanupを追記し、オブジェクト再作成によるローカル検証とHTTP途中再開・実Worker再起動の未確認範囲を明記した。

- Discord差分E2Eをキャンセル・削除・archive読戻しへ拡張し、通常のキャンセル判定、ローカル回帰と実サービス未確認の境界を追記した。

- Discord差分E2Eの手動モード、専用route、所有資源と状態分離、cleanup、検証境界を `docs/TESTING.md` と `docs/BACKEND.md` に追加した。
- `docs/ISSUES.md` と `docs/WORKLOG.md` に、ローカル検証までの進捗と実サービス実行が未確認であることを記録した。

## 2026-09-02

### 変更

- `AGENTS.md` の追跡対象外文書4件をパス表記へ変更し、クリーンなチェックアウトで相対リンク切れが生じないようにした。
- ローカル補助文書を追跡対象外で維持する方針と、標準文書を正本とする境界は変更していない。
- E2E Worker の未所有な通常同期・実Webhook・通常ジョブ route を専用フラグで既定拒否し、status と preflight で無効状態を確認するようにした。
- 自己 cleanup 型のサービス間 E2E を `docs/ISSUES.md` と GitHub Issue #17 で追跡するようにした。
- 所有資源を専用 Google event と Notion page に限定し、既存の適用処理を通して検証・cleanupする Google→Notion E2E mode を追加した。
- 所有資源を専用 Google event と Discord Scheduled Event に限定し、既存の適用処理を通して検証・cleanupする Google→Discord E2E mode を追加した。
- 所有資源を専用 Discord Scheduled Event と Notion page に限定し、既存の適用処理を通して検証・cleanupする Discord→Notion E2E mode を追加した。
- 所有資源を専用 Discord Scheduled Event と Google event に限定し、既存の適用処理を通して検証・cleanupする Discord→Google E2E mode を追加した。
- 所有資源を専用Notion Q&A pageとDiscord messageに限定し、通常ジョブと共通の初回抑止・更新通知処理を検証・cleanupするQA通知E2E modeを追加した。
- 所有資源を専用 Discord Scheduled Event と message に限定し、通常ジョブと共通の通知ウィンドウ判定・重複抑止を検証・cleanup する前日リマインド E2E mode を追加した。
- 所有資源を専用 Notion 内部 DB の期限到来・将来日時 page に限定し、通常ジョブと共通の期限判定・interval guard を検証・cleanup する Notion期限cleanup E2E mode を追加した。
- Notion pageの日時読戻しは文字列表現ではなく、timezone付きRFC 3339をUTCの同一時刻として検証するようにした。
- Notion cleanup fixtureの日時を既存Notion CRUDと同じ分境界へ揃え、読戻し不一致時は値を出さず項目名だけを固定エラーに残すようにした。
- 所有資源を専用Google eventとNotion pageに限定し、Google差分取得と通常同期dispatchを検証・cleanupするWebhook simulation E2E modeを追加した。
- 通常の `/sync/all`、通常Webhookの同期dispatch、ジョブを既定拒否のまま維持し、新しい scenario が保証しない共有KV状態、変更起因Webhook / Cron の境界を明記した。
- required reviewer承認付きの専用workflowでGoogle→Notion scenarioを実行し、両資源cleanupとマスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- required reviewer承認付きの専用workflowでGoogle→Discord scenarioを実行し、両資源cleanupとマスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- required reviewer承認付きの専用workflowでDiscord→Notion scenarioを実行し、両資源cleanupとマスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- required reviewer承認付きの専用workflowでDiscord→Google scenarioを実行し、両資源cleanupとマスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- required reviewer承認付きの専用workflowでQA通知scenarioを実行し、Notion pageとDiscord messageのcleanup、マスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- required reviewer 承認付きの専用 workflow で前日リマインド scenario を実行し、Discord Scheduled Event と message の cleanup、重複抑止、マスク済み artifact を確認した結果を作業履歴と課題へ記録した。
- required reviewer付きの専用workflowでNotion期限cleanup scenarioを実行し、期限判定、interval guard、両pageのcleanup、マスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- required reviewer付きの専用workflowでWebhook simulation scenarioを実行し、Google差分取得、所有eventだけのNotion適用、共有状態の分離、両資源cleanup、マスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- Webhook simulationを通常Workerと共通のingress handlerへ拡張し、channel tokenの事前拒否、Durable Objectでのmessage重複抑止、run所有重複状態のcleanupを検証できるようにした。
- E2E deployへrun IDのWorker version tagを付与し、同じtagを`/admin/e2e/status`から読み戻すまで外部書き込みscenarioを開始しないrevision gateを追加した。
- required reviewer付きの専用workflowでWebhook ingress simulationを再実行し、revision tag、token拒否、message重複抑止、run所有重複状態を含む全資源cleanup、マスク済みartifactを確認した結果を作業履歴と課題へ記録した。
- run所有の短命Google watchを作成し、初回`sync`通知の実配信確認後に停止する自己cleanup型E2E modeを追加した。通常同期dispatch、共有状態、watch維持、実Cronとは分離した。
- run所有eventの更新でGoogleの実`exists`通知を発生させ、共通Webhook ingressと同期dispatchからその1件だけをNotionへ適用・回収する自己cleanup型E2E modeを追加した。共有cursor、全件適用、通常watch更新、実Cronとは分離した。
- required reviewer付きの専用workflowでGoogle Webhook初回実配信scenarioを実行し、revision tag、watch作成、初回通知、watch停止、`dirty=false`、マスク済みartifactを確認した結果を作業履歴、課題、セキュリティ境界へ記録した。
- required reviewer付きの専用workflowでGoogle変更起因Webhook scenarioを実行し、実`exists`通知、所有event限定のNotion適用、実行内状態の分離、watchと全所有資源のcleanup、`dirty=false`、マスク済みartifactを確認した結果を作業履歴、課題、セキュリティ境界へ記録した。

## 2026-08-29

### 追加

- 外部通信を遮断するローカル単体テスト基盤と主要同期制御のテスト。
- `docs/TESTING.md`。
- Google Webhook channel token の登録・照合と、token 変更時の watch 再登録。
- `package.json` と `package-lock.json` による Wrangler の版固定。
- `docs/DEVELOPMENT.md`
- `docs/REQUIREMENTS.md`
- `docs/FRONTEND.md`
- `docs/BACKEND.md`
- `docs/SECURITY.md`
- `docs/DB-SCHEMA.md`
- `docs/REFERENCES.md`
- `docs/ISSUES.md`
- `docs/GOAL.md`
- `docs/WORKLOG.md`
- `docs/CHANGELOG.md`

### 変更

- CI で `pytest -q` を常時実行するように変更。
- 内部 API と Google Webhook の認証を、Secret 未設定時も処理を開始しない fail-closed へ変更。
- Google watch API の外部エラー本文を管理応答と状態履歴へ流さないように変更。
- `AGENTS.md` をテンプレートの指示優先順位と Markdown 目次へ統合し、日本語化。
- 既存の WSL 境界、Linux 仮想環境、Cloudflare Workers、依存関係、検証規則を保持。
- `docs/do-kv-design.md` の英語本文を日本語化。
- `docs/Operations.md` と `docs/KV.md` のコマンド例を WSL / Linux 向けに統合。

### 保持

- 既存の README、仕様、KV、運用、Fork / Upstream 文書。
- 作業開始前から存在した未コミット変更。
- `docs/Event_Bot仕様書.md`、`docs/KV.md`、`docs/Operations.md`、`docs/do-kv-design.md` を追跡対象外のローカル補助とする方針。

## 2026-09-25: 実Cronと手動同期の競合E2E

- 実Cronと別HTTPの両方向のロック競合、解放後の再実行、結果KV読戻し、所有資源回収を検証した。[実行36104059809](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36104059809)で成功し、[計画](E2E-PLAN.md)項目8と[検証記録](E2E-CRON-CONTENTION.md)を更新した。

## 2026-09-25: 通常ジョブ再試行

- Q&Aの通知失敗をcacheで抑止する問題とcleanup失敗後のinterval guardを修正。3ジョブの固定失敗・別HTTP回復・重複抑止・回収を実サービスで確認した（[検証記録](E2E-JOBS-RETRY.md)）。

## 2026-09-25: 通常ジョブのKV保存再試行

- 通常ジョブのKV6キーだけを同じ値で最大3回保存し、上限到達時の失敗応答とCron後続処理を整えた。
- 固定失敗・回復・重複抑止・共有KV読戻し・全資源回収を専用E2Eで確認した。[検証記録](E2E-JOBS-KV-RETRY.md)。
