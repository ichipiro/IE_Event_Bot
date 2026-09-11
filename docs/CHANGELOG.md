# 文書変更履歴

この文書は、標準文書構成の変更を記録する。アプリケーションの公開リリース履歴は、Release Please が管理するリポジトリルートの `CHANGELOG.md` を正本とする。

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
