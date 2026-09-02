# 課題

## 運用方法

この文書には、現行コード、設定、文書から確認できた課題と解決結果を記録する。GitHub、Cloudflare、Discord、Google、Notion の現在状態が必要な項目は、確認日と検証方法を併記する。

状態は `未対応`、`対応中`、`確認待ち`、`完了` のいずれかを使う。

## 課題一覧

### ローカル単体テスト基盤

- 状態: 完了
- 根拠: `tests/` に外部通信を遮断するテスト基盤と、認可、クールダウン、ロック、Webhook 重複、キュー繰り越しの単体テストを追加した。
- 対応: CI で `pytest -q` を常時実行し、テストが収集されない状態を成功扱いにしない。
- 継続方針: watch、認証ソース、定期ジョブ、外部 API 応答別のテストは、各機能変更時に拡張する。

### `INTERNAL_API_TOKEN` 未設定時に同期・管理・ジョブ API が公開される

- 状態: 完了
- 根拠: `workers/src/entry.py` の `_authorized()` は、`INTERNAL_API_TOKEN` が未設定、空、欠落、不一致のいずれでも認可に失敗する。
- 対応: 同期、管理、ジョブ API は処理開始前に `401` を返す fail-closed とし、未設定時の回帰テストを追加した。
- 実環境境界: Cloudflare 上の Secret 登録状態はローカルでは未確認であり、デプロイ時に別途確認する。

### Google Webhook の送信元認証が限定的

- 状態: 完了
- 根拠: Google watch 登録時に `GCAL_WEBHOOK_TOKEN` を channel token として設定し、受信時に `X-Goog-Channel-Token` と照合する。
- 対応: Secret 未設定時は `503`、ヘッダー欠落・不一致時は `401` を返し、同期と重複状態更新の前に拒否する。旧 watch または token 変更時は SHA-256 fingerprint の不一致から再登録する。
- 多層防御: token 検証に加えて、重複抑止、クールダウン、Durable Object ロックを維持する。Cloudflare WAF とレート制限は実環境で必要性を判断する。
- 実環境境界: token 付き watch の再登録と Google からの実通知はローカルでは未確認であり、デプロイ時に別途確認する。

### ローカル詳細文書の追跡方針が分かれている

- 状態: 完了
- 根拠: `docs/Event_Bot仕様書.md`、`docs/KV.md`、`docs/Operations.md`、`docs/do-kv-design.md` は存在するが `.gitignore` 対象である。
- 決定: 4文書は追跡対象外のローカル補助として維持する。削除、追跡追加、本文変更は行わない。
- 対応: クリーンなチェックアウトで必要な現行要件は追跡対象の標準文書へ記載し、ローカル補助だけを正本にしない。
- 検証: `AGENTS.md` では4文書をリンクではなくパス表記とし、追跡対象 Markdown の相対リンクがクリーンなチェックアウトで解決する状態を保つ。

### Wrangler のローカル版が固定されていない

- 状態: 完了
- 根拠: `package.json` で Wrangler `4.127.1` を完全固定し、`package-lock.json` で解決済み依存と整合性を固定した。
- 対応: `npm ci` でローカル版を導入し、`npm run wrangler -- ...` で実行する。グローバル Wrangler は前提にしない。
- 検証: 固定版の `--version`、依存監査、`workers/wrangler.jsonc` を指定した `deploy --dry-run` が成功した。実デプロイは行っていない。

## 外部状態を伴う課題

Fork、Upstream、GitHub Actions、Release Please、branch protection の確認結果は `docs/fork-upstream-workflow.md` に記録されている。これらは変化し得るため、作業前に GitHub 上の現在状態を再確認する。

### サービス間同期・Webhook・定期ジョブの自己cleanup型 E2E

- 状態: 対応中
- 対応済み範囲: Google→Notion は、専用 Google event と Notion page を同じ強整合 manifest で所有し、既存のアプリケーション適用処理を通した検証と自己 cleanup を行う専用 scenario を実装した。2026-09-02の[専用E2E workflow](https://github.com/lycanthr0pes/IE_Event_Bot_fork/actions/runs/33579456642)でdeploy、実サービス適用、両資源cleanup、マスク済みartifactの独立確認まで成功した。
- 対応済み範囲: Google→Discord は、専用 Google event と Discord Scheduled Event を同じ強整合 manifest で所有し、既存の `_sync_to_discord` を通した検証と自己 cleanup を行う専用 scenario を実装した。通常 KV の同期対応表と queue、Notion は変更対象にしない。実サービスでの実行は未確認である。
- 未対応範囲: Google 差分取得と cursor、全体同期、Webhook simulation、実 webhook / Cron、定期ジョブが変更する下流資源と状態には run ID が伝播していない。
- 暫定対応: 未対応の通常同期、Webhook simulation、ジョブは `E2E_ORCHESTRATED_WRITES_ENABLED=false` で `404` にする。read-only preflight、service CRUD、所有資源限定の Google→Notion / Discord scenario は別 route で継続する。
- 完了条件: 全下流資源と状態を強整合 manifest で所有し、run ID と対象 fingerprint の一致後だけ cleanup できること。simulation と実 webhook / Cron 配信の証拠は分けること。
- 追跡: [GitHub Issue #17](https://github.com/lycanthr0pes/IE_Event_Bot_fork/issues/17)
