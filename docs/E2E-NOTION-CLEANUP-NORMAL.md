# 通常Notion cleanupのE2E

`deploy-and-notion-cleanup-normal-smoke` は、空の専用Notion内部DBへ期限切れと将来日時のページを各1件作成し、通常HTTPハンドラと共有KVを検証する。
既存の所有ページ限定モードは維持する。基本手順は [TESTING.md](TESTING.md) を参照。

## 実行経路

手動workflow → MCP `trigger_job(job="cleanup_normal_<phase>")` → 認証・run/revision照合・globalロック付き管理route → 通常 `Application.fetch` の `/jobs/cleanup` 分岐 → `run_auto_clean_job`。

| 段階 | 処理と確認 |
| --- | --- |
| `prepare` | DB schema・空DB・共有キー未設定を確認し、所有ページ2件を作成する |
| `execute` | 通常ジョブがDBを無加工で全件取得し、`scanned=2`・`archived=1`を返す。期限切れだけをarchiveする |
| `duplicate` | 別HTTPで通常ジョブを再実行し、`interval_guard`でskipする。最終時刻は書き直さない |
| `verify` | 各段階後、別HTTPでページ内容・archive状態・共有KV・通常ジョブ結果を読む |
| `cleanup` | 両ページをarchiveし、今回書き込んだ共有キーだけを削除・読戻しする |

通常 `StateStore` が使う `cleanup:last_epoch` と `result:job_cleanup` は、専用環境の実KVの同名キーへ保存する。
KVアダプターは許可キーと所有runを照合し、値のdigestを書込み前にDOへ記録する。
初回の最終時刻の書込みは1回、結果の書込みは通常実行とskipの計2回である。
DOは所有権・回収記録と他scenarioとの排他を保持する。KVの代わりにジョブ状態を返すものではない。

## 保護と回収

- DBが空でない場合、共有キーが設定済みの場合、別runやrevisionの場合は開始しない。
- 通常ジョブ直前も一覧全体が期待する所有ページだけであることを確認する。
- 開始から4分を超えた通常ジョブ実行を拒否し、最低300秒の実行間隔内で再実行抑止を確認する。
- 書込み処理は自動再送せず、KV反映待ちの読戻しだけを上限付きで再試行する。
- 作成応答を失ったページはrun markerで再発見する。所有権未解決・所有外KV・回収未確認ならdirtyを維持する。
- 全段階と各verify・回収が成功した場合だけ `outcome=passed`・`dirty=false` を保存する。
- workflow終了時にも監査から今回触ったscenarioを特定し、常時cleanupを実行する。

本番資源・実Cronは対象外。これは専用DBの2件の試験であり、100件超のページ送り、外部API実障害、通常ジョブの失敗後の再試行、KVの全リージョン一貫性は証明しない。
外部から通常URLへ直接アクセスする試験ではなく、保護管理routeから通常HTTPハンドラへ委譲する。

## ローカル検証

2026-09-25（JST）時点で、Python 868件・Node 305件、Ruff、Pyright、E2E設定・機密保護・workflow検査、E2E構成のWrangler dry-runが成功した。
所有外ページ・共有KV、別run・revision、段階の再送、作成応答喪失、archive失敗、KV書込み応答喪失、期限超過、他scenarioとの相互排他を外部通信なしで確認した。
実サービスの成功はworkflow・manifest・監査・回収の独立照合後に記録する。
