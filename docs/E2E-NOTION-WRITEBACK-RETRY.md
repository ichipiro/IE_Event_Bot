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

## 実サービス試験結果

[実行36147796164](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36147796164)は初回で成功した。

- run: `E2E-20260925T143153Z-5c34160b`
- commit: `41a618e57cb249eac1f5e0a53c79cf1f70b67ec6`。実行checkoutはclean、`e2e` Environmentの承認記録も確認した。
- Worker version SHA-256: `db1817ebf21db53b2960e2ca1e16921c4770fc76aaa98cbcff6fedf13499e274`。deploy監査と最終読戻しが一致し、version tagもrun IDと一致。
- 全4段階と別HTTPのverify、計8HTTPが成功。実Notion APIの400・`validation_error`、通常dispatchの500、queue2件の内容・順序とcursor・最終成功時刻の維持を確認した。
- 失敗時にNotion・Discord各2件と対応表の追加を読み戻し、対象ページのDiscord IDだけが空であることを確認した。
- 次HTTPで保存queue2件だけを消化し、queue0件、Notion・Discord各3件となった。部分反映した2件目も同じページ・イベントIDを再利用して書戻しを完了した。
- 全3件の再適用後も同じ対応ID・各3件で重複はなかった。
- Google予定3件・Discord予定3件を削除、Notionページ3件をarchiveし、それぞれGETで確認した。共有KV6キーは削除後の不在を確認した。
- 今回scenarioは `outcome=passed`。全service／scenario manifestが `dirty=false`、watch不在。本体と後処理の回収2回とも成功し、失敗した回収はなかった。
- 監査22行・11操作、検証stage48件、run/version/commit、JUnit1,117件・失敗0を独立照合した。CIのNode381件・Cron契約13件と静的検査・E2E契約検査・dry-runも成功。

秘匿済み成果物と独立照合スクリプトは `test-results/notion-writeback-retry-36147796164/`、照合結果は同ディレクトリの `verification.json` に保存した。
入力検証による書戻し拒否からの復旧を確認した。自然発生障害、Notion作成直後のページUUID書戻し失敗、応答喪失、本番反映は本実行に含めない。
