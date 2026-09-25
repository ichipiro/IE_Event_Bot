# Google同期の17件・上限5件E2E

専用E2E環境で `deploy-and-google-boundary-smoke` を実行する。
通常Google同期の件数上限、共有queue、対応ID、cursor更新後の残件維持を検証する。

## 試験の境界

- Google Calendar・Notion DB・Discord Guildと共有KVが空であることを確認してから開始する。開始前の削除履歴は最大100件をfingerprintで識別する。
- run/version・対象fingerprintを確認し、固定17件の所有予定をDO manifestへ先に保存する。
- step 0は準備、1〜17はGoogle予定を1件ずつ作成し、各回を別HTTPで読む。
- step 18は通常の全ページ取得で得た17件を共有queueへ準備し、cursor未更新・同期先未作成を別HTTPで確認する。この初期queue保存は試験準備の操作である。
- step 19〜22は通常dispatch・通常適用へ上限5件を渡し、5・5・5・2件を処理する。Google取得は実APIを使うが、適用への新規入力を空にし、保存済みqueueだけで17→12→7→2→0へ進むことを確認する。
- 上限繰越は通常実装上の成功であり、残件があってもcursorを更新する。各回の期待cursor、全対応ID、残件のID・内容、結果を共有KVから別HTTPで確認する。API失敗時のcursor保持は別の試験で扱う。
- step 23〜26は全17件のGoogle・Notion・Discordを最大5件ずつ読み直す。本文、日時、対応ID、NotionのDiscord ID書戻しを照合する。
- 回収は最大5件ずつ行い、各資源の削除・archiveをGETで確認後に完了を記録する。残件がある間は `google_sync_cleanup_pending`・`dirty=true` とし、4回のHTTPで全件を回収する。最後に所有共有KV6キーの不在を確認する。
- 全27段階の検証と回収が成功した場合だけ `passed` とする。途中終了の回収成功は `failed_clean` とする。

認証情報や外部資源のraw IDは成果物へ含めない。本番Workerのデプロイ、任意件数・任意構成、自然発生障害を確認済みとは扱わない。

## ローカル検証

`tests/test_e2e_google_boundary.py` は通常dispatch、通常適用、DOの所有権判定を使い、外部APIだけを代替する。
17件の残件推移、既存対応IDの保持、削除履歴100件、途中回収、所有記録の改変拒否、queue改変時の停止、適用失敗時のcursor保持と回収を検証する。
Node試験は専用route、HTTP待機時間、27段階の証跡必須判定、回収の繰返しを確認する。
実サービス結果はworkflow・artifactの独立照合後に追記する。

関連: [試験計画](E2E-PLAN.md)、[残試験の棚卸し](E2E-AUDIT-20260925.md#残試験の具体化)。

## 実行記録

初回[36140023272](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/36140023272)はcommit `4977d5c`、run `E2E-20260925T131955Z-160fe439`。
step 0のWorker検証は成功したが、verify応答のstage名を `verified` としたため、MCPが要求する `google_prepared_verified` と不一致になり停止した。予定作成前の停止であり、件数境界の成功ではない。
最終artifactから `failed_clean`・全manifest `dirty=false`・共有KV回収・watch不在を独立確認した。Node/Python試験の代替応答だけではこの接続不一致を検出できていなかった。
Workerの実応答名を確認する再現テスト3件が修正前に失敗し、既存MCP契約に合わせて修正した。
