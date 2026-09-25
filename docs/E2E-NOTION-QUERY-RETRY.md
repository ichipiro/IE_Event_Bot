# Notion照会失敗後の復旧E2E

専用環境で `deploy-and-notion-query-retry-smoke` を実行する。
入口は `/admin/e2e/google-sync/notion-query`、続行・読戻し・回収は既存のGoogle同期E2E入口を使う。
共有KVと通常Google dispatch・適用処理を通し、run/version・所有対象をDO manifestで照合する。

## 段階と判定

| step | 操作 | 別HTTPの確認 |
| --- | --- | --- |
| 0 | 空の専用環境へ所有Google予定3件を作成し、上限1件で通常同期 | Notion・Discord各1件、共有queue2件、対応ID・cursor |
| 1 | queue先頭のNotion照会へ不正な `page_size` を送る | 実APIの400・`validation_error`、通常dispatchの500、queue2件と順序・対応表・cursor・最終成功時刻の維持、同期先の追加作成なし |
| 2 | 取得した新規入力を空にして、保存queue2件だけを再試行 | queue0件、Notion・Discord各3件、既存ID維持、Discord ID書戻し |
| 3 | 所有Google予定3件を再適用 | ID維持、Notion・Discord各3件、重複なし |
| cleanup | 所有Google・Discord予定を削除、Notionページをarchive、共有KV6キーを回収 | 各資源のGETとKV読戻し、`passed`・`dirty=false` |

失敗の注入は認証・照会先・GoogleイベントIDのfilterを維持し、要求bodyの型だけを変更する。
実応答を通常のNotion照会へ渡すため、通常の例外処理と失敗queue保存を通る。
400以外、異なるerror code、再試行失敗、重複や所有外資源は試験成功にしない。
途中回収は `failed_clean` とし、最終段階の別HTTP検証が完了した場合だけ `passed` とする。

Notion APIの[照会仕様](https://developers.notion.com/reference/post-database-query)と[エラーコード](https://developers.notion.com/reference/status-codes)を参照。
これは不正入力へのAPI拒否後の回復であり、自然発生障害や回線断の観測ではない。
通常 `/sync/all` のHTTP入口、実Cron・実Webhook、Notion作成失敗・Discord ID書戻し失敗は対象外。
取得は実APIを通すが、step 1・2では適用への新規入力を空にしてqueue単独の回復を検証する。

関連: [E2E計画](E2E-PLAN.md)、[残試験](E2E-AUDIT-20260925.md#残試験の具体化)。

## 実行記録

初回[実行36143962340](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36143962340)はcommit `f7c53bd`、run `E2E-20260925T135630Z-b8e008ac`。
事前検証は成功したが、専用Calendarの削除履歴が100件を超え、`google_sync_baseline_limit` で資源作成前に停止した。
今回runのmanifestは作成されていない。既存manifestの `passed` は前回試験の結果であり、今回の成功ではない。
回収要求8回は別runのclean manifestを変更せず `google_sync_run_mismatch` で拒否された。最終artifactの全manifestは `dirty=false`、今回の新規資源はない。

修正版は今回の試験に限り、削除履歴のIDを含むイベント全体のSHA-256を一覧で保存する。
最大256件まで許可し、ID別digestの二重保存を避ける。履歴の内容変更・所有外予定・257件以上は拒否し、既存履歴を削除しない。
256件を保持した全段階・回収と32 KiB未満のmanifestをローカルで確認した。従来モードの100件制限は維持する。

### 再試験成功

[実行36144536848](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36144536848)が成功した。

- run: `E2E-20260925T140203Z-79ddd791`
- commit: `fb97396d4fd34f4870b96980d7cab7e1cc53d3c1`。実行checkoutはclean。
- Worker version SHA-256: `a48c3fa2d9df968ea7d1cf84bac04c2b52e6ad31465bc712890dcb99ad8c13ba`。deploy監査と最終読戻しが一致し、version tagもrun IDと一致。
- 全4段階・操作とverifyの計8HTTPが成功。Notionの400・`validation_error`、通常dispatchの500、queue2件の内容と順序・対応表・cursor・最終成功時刻の維持を確認した。
- 別HTTPで保存queue2件だけを消化し、queue0件、Notion・Discord各3件、既存ID維持とDiscord ID書戻しを確認した。全3件の再適用後も各3件・同一IDで重複はなかった。
- Google予定3件・Discord予定3件を削除、Notionページ3件をarchiveし、それぞれGETで確認した。共有KV6キーは削除後の不在を確認した。
- 今回scenarioは `outcome=passed`。全service／scenario manifestが `dirty=false`、watch不在、回収は本体と後処理の2回とも成功した。
- 監査22行・11操作、検証stage44件、run/version/commit、JUnit1,073件・失敗0を独立照合した。Node354件、Cron契約13件、Ruff・Pyright・E2E契約検査・dry-runも成功。

取得証跡は `test-results/notion-query-retry-36144536848/`、独立照合結果は同ディレクトリの `verification.json` に保存した。
初回の停止・回収拒否の証跡は `test-results/notion-query-retry-36143962340/prewrite-verification.json` に分けて保存した。
Notion照会のAPI拒否後の復旧は確認済み。Notion作成・Discord ID書戻しのAPI拒否、自然発生障害、本番反映は本実行に含めない。
