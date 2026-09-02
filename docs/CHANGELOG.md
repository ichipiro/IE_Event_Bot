# 文書変更履歴

この文書は、標準文書構成の変更を記録する。アプリケーションの公開リリース履歴は、Release Please が管理するリポジトリルートの `CHANGELOG.md` を正本とする。

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
