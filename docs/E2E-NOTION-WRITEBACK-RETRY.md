# NotionへのDiscord ID書戻し失敗後の復旧E2E

専用環境で `deploy-and-notion-writeback-retry-smoke` を実行する。
入口は `/admin/e2e/google-sync/notion-writeback`、続行・読戻し・回収は既存のGoogle同期E2E入口を使う。
通常Google dispatch・適用処理・共有KVを通し、run/versionと所有対象をDO manifestで照合する。

| step | 操作 | 別HTTPでの確認 |
| --- | --- | --- |
| 0 | 所有Google予定3件を作成し、上限1件で同期 | Notion・Discord各1件、queue2件、対応ID・cursor |
| 1 | queue先頭のNotion・Discord作成後、Discord ID書戻しの `rich_text` を不正な文字列にする | 実APIの400・`validation_error`、通常dispatchの500、queue2件の内容・順序とcursor・最終成功時刻の維持。Notion・Discord各2件と対応表の追加を確認し、失敗ページのDiscord IDだけが空であることを確認 |
| 2 | 新規取得入力を空にして保存queue2件だけを再試行 | queue0件、Notion・Discord各3件。部分反映したページ・イベントの同じIDを使って書戻しが完了 |
| 3 | 全所有Google予定を再適用 | 全対応ID維持、Notion・Discord各3件、重複なし |
| cleanup | Google・Discord予定を削除、Notionページをarchive、共有KV6キーを回収 | 各資源のGETとKV読戻し、`passed`・`dirty=false` |

失敗注入は書戻し要求の対象ページを再取得して専用DB・GoogleイベントID・所有マーカーを照合する。
ページID・認証を保持し、`メッセージID.rich_text` の型だけを変更して実応答を通常処理へ返す。
照会・作成拒否と異なり、部分反映したNotion・Discord対応表の追加を許可し、既存対応表の全項目は保持する。
400以外、異なるerror code、再試行失敗、重複、所有外資源を成功扱いしない。
モード切替・必須条件の削除はmanifestの所有条件で拒否する。

途中回収は `failed_clean`、最終段階の別HTTP検証と回収が完了した場合だけ `passed` とする。
Notion APIの[ページ更新仕様](https://developers.notion.com/reference/patch-page)と[エラーコード](https://developers.notion.com/reference/status-codes)を参照。
これは入力検証によるAPI拒否後の復旧であり、自然発生障害や回線断の観測ではない。
通常 `/sync/all` のHTTP入口、実Cron・実Webhook、本番反映は対象外。

関連: [照会復旧E2E](E2E-NOTION-QUERY-RETRY.md)、[作成復旧E2E](E2E-NOTION-CREATE-RETRY.md)、[E2E計画](E2E-PLAN.md)。

## 事前検証

ローカルPython 1,117件、Node 381件、Cron契約13件、Ruff・Pyright、依存import、E2E設定・機密保護・workflow契約検査、Wrangler E2E dry-runが成功。
Pyrightは仮想環境の有効化後に成功した。直接起動時の外側Python参照によるimport解決エラーとは区別する。
外部APIを代替したローカル結果は、実サービス試験の成功を示さない。
