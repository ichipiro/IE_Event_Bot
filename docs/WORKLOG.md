# 作業履歴

## 記録方針

- 目的、変更した文書または機能、実施した検証、未確認事項を簡潔に記録する。
- Git のコミット履歴を置き換えず、作業の判断と検証境界を補足する。
- シークレット、個人情報、外部サービスの認証値を記録しない。

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

- 未実施。upstreamとforkのレビュー経路へ反映後、required reviewer付きの専用workflowで確認する。

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
