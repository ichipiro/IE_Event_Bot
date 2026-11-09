# Notionページ作成失敗後の復旧E2E

専用環境で `deploy-and-notion-create-retry-smoke` を実行する。
入口は `/admin/e2e/google-sync/notion-create`、続行・読戻し・回収は既存のGoogle同期E2E入口を使う。
通常Google dispatch・適用処理・共有KVを通し、run/versionと所有対象をDO manifestで照合する。

| step | 操作 | 別HTTPでの確認 |
| --- | --- | --- |
| 0 | 空の専用環境へ所有Google予定3件を作成し、上限1件で通常同期 | Notion・Discord各1件、共有queue2件、対応ID・cursor |
| 1 | queue先頭のNotionページ作成で `children` を不正な文字列にする | 実APIの400・`validation_error`、通常dispatchの500、queue2件の内容と順序・対応表・cursor・最終成功時刻の維持、追加作成なし |
| 2 | 新規取得入力を空にして保存queue2件だけを再試行 | queue0件、Notion・Discord各3件、既存ID維持、Discord ID書戻し |
| 3 | 所有Google予定3件を再適用 | 全対応ID維持、Notion・Discord各3件、重複なし |
| cleanup | Google・Discord予定を削除、Notionページをarchive、共有KV6キーを回収 | 各資源のGETとKV読戻し、`passed`・`dirty=false` |

失敗注入はDB、GoogleイベントID、所有マーカー、認証を保持し、ページ作成要求の `children` の型だけを変更する。
実API応答を通常作成処理へ返し、`notion_internal_create_failed` と未処理queue保存を確認する。
400以外、異なるerror code、再試行失敗、重複、所有外資源を成功扱いしない。
照会失敗モードとはmanifestの所有条件と必須証跡を分け、途中での切替を拒否する。
削除履歴はイベント全体のSHA-256で最大256件を保持し、既存履歴を変更しない。

途中回収は `failed_clean`、最終段階の別HTTP検証と回収が完了した場合だけ `passed` とする。
Notion APIの[ページ作成仕様](https://developers.notion.com/reference/post-page)と[エラーコード](https://developers.notion.com/reference/status-codes)を参照。
これは入力検証によるAPI拒否後の復旧であり、自然発生障害や回線断の観測ではない。
通常 `/sync/all` のHTTP入口、実Cron・実Webhook、Discord ID書戻し失敗は対象外。

関連: [照会復旧E2E](E2E-NOTION-QUERY-RETRY.md)、[E2E計画](E2E-PLAN.md)、[残試験](E2E-AUDIT-20260925.md#残試験の具体化)。

## 事前検証

ローカルPython 1,090件、Node 366件、Cron契約13件、Ruff・Pyright、E2E設定・機密保護・workflow契約検査、Wrangler E2E dry-runが成功。
外部APIを代替したローカル結果は、実サービス試験の成功とは区別する。

## 実サービス試験結果

[実行36145925999](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36145925999)は初回で成功した。

- run: `E2E-20260925T141553Z-50109a17`
- commit: `ed0251e2b08d4e6d99f082ebca2526ab13e6a693`。実行checkoutはclean。
- Worker version SHA-256: `20f0b72ae33e18beff4d5bebab780e16d841183efcc827724fe773ee5b1022b6`。deploy監査と最終読戻しが一致し、version tagもrun IDと一致。
- 全4段階と別HTTPのverify、計8HTTPが成功。実Notion APIの400・`validation_error`、通常dispatchの500、queue2件の内容と順序・対応表・cursor・最終成功時刻の維持を確認した。
- 次HTTPで保存queue2件だけを消化し、queue0件、Notion・Discord各3件、既存ID維持とDiscord ID書戻しを確認した。全3件の再適用後も同じID・各3件で重複はなかった。
- Google予定3件・Discord予定3件を削除、Notionページ3件をarchiveし、それぞれGETで確認した。共有KV6キーは削除後の不在を確認した。
- 今回scenarioは `outcome=passed`。全service／scenario manifestが `dirty=false`、watch不在。本体と後処理の回収2回とも成功し、失敗した回収はなかった。
- 監査22行・11操作、検証stage44件、run/version/commit、JUnit1,090件・失敗0を独立照合した。Node366件・Cron契約13件と静的検査・E2E契約検査・dry-runも成功。

秘匿済み成果物と独立照合スクリプトは `test-results/notion-create-retry-36145925999/`、照合結果は同ディレクトリの `verification.json` に保存した。
入力検証によるページ作成拒否からの復旧を確認した。自然発生障害、Discord ID書戻しのAPI拒否、本番反映は本実行に含めない。
