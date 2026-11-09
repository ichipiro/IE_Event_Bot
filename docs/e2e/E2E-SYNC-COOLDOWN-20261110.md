# 全体同期の取得後クールダウン修正・検証記録

## 対象と状態

2026-11-09 13:36 JSTのイベント作成通知の重複調査で、正常終了する同期2回が同じ通知を投稿できる条件を再現した。今回の修正は、そのうち開始判定とロック取得の間の競合を対象とする。本番の発生順の確定とは区別する。

実装commitは `d310ca8d0feb0eb1421ab376abc6c9fd69e0976c`。リリース・本番反映は対応中であり、この時点では完了していない。先の承認済み同期修正・E2E整理を含むfeatureから、[正規フロー](../fork-upstream-workflow.md)で統合する。

## 修正とローカル検証

global DOのロック取得後、ownerとDOの最終成功時刻を確認し、クールダウン中なら同期本体へ進まない。manual・Cronは200、Webhookは503の既存の`cooldown_skip`を維持し、ロックはfinallyで解放する。

[回帰テスト](../../tests/test_sync_guarantee_boundaries.py)の `test_post_lock_cooldown_prevents_duplicate_notification` は、旧実装で2件失敗・1件成功、修正後は3件すべて成功した。取得前の競合と古いsnapshot、競合と最新snapshot、300秒以内の直列実行で、同一イベントのPOST・リアクションは1回、後続のsnapshot読取りは0回、ロック残留は0件となる。

- Python: 1,243件成功。外部通信は代替実装で遮断。
- Node: MCP・workflow契約424件、Cron契約13件成功。
- Ruff・Pyright、E2E設定・機密保護・workflow検査成功。
- 通常Worker・専用E2E WorkerのWrangler dry-run成功。実デプロイではない。

## 実サービス検証

通知専用、all-sync、all-http、watch-sharedの4モードを今回修正版で再実行する。実行番号・source SHA・run/version一致、所有資源の回収を照合した後に結果を追記する。以前の成功実行は今回の実績へ数えない。

## 保証の境界

今回の競合の直接証拠は、通常Application・StateStore・同じglobal DOを通したローカル回帰である。実行順と古い読取りは固定モデルであり、実KVの遅延や本番での発生頻度を測っていない。

全体同期E2Eは作成通知を無効化し、通知専用E2Eは追加のDO所有記録で資源を保護する。そのため、実E2Eの成功も任意の通常通知の一度だけの配信保証ではない。単独Discord同期、300秒経過後の古いsnapshot、投稿応答喪失・投稿後の保存失敗、別namespace、ロック期限超過の限界は[保証範囲](../TESTING.md#状態障害の保証範囲表)に残す。
