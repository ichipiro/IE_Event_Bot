# ローカルテスト

## 目的

この文書は、IE Event Bot の単体テストを Linux / WSL の CPython 上で再現する手順と、テストで保証できる境界を定義する。

テストは Cloudflare、Discord、Google、Notion の実環境へ接続しない。認証情報も使用しない。

## セットアップ

リポジトリルートの仮想環境へ開発依存を導入する。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install -r workers/requirements.txt
```

既存の `.venv` が利用できる場合は、作り直す必要はない。

## 実行方法

全テスト:

```bash
.venv/bin/python -m pytest -q
```

ファイル単位:

```bash
.venv/bin/python -m pytest -q tests/test_entry.py
```

テスト単位:

```bash
.venv/bin/python -m pytest -q \
  tests/test_entry.py::test_webhook_duplicate_dispatches_only_once
```

CI は Ruff、Pyright の後に全テストを実行する。テストが収集できない場合も成功扱いにはしない。

E2E オーケストレーター MCP のローカル契約テストと設定検査:

```bash
npm run test:mcp
.venv/bin/python tools/validate_e2e_mcp_config.py
.venv/bin/python tools/validate_e2e_secret_hygiene.py
.venv/bin/python tools/validate_e2e_workflow.py
bash -n tools/configure_github_e2e_environment.sh
```

これらも通信先をテスト代替へ差し替えるか、設定ファイルだけを読む。実 Worker や外部サービスへは接続しない。

## 手動 E2E workflow

`.github/workflows/e2e-staging.yml` は `workflow_dispatch` 専用であり、PR、push、schedule からは起動しない。forkではこのworkflowを登録するため、既定ブランチを`develop`とする。最初の job でローカル検査と Wrangler dry-runを行い、成功後に `e2e` GitHub Environment の承認を待つ。`GITHUB_TOKEN` は `contents: read` だけに限定する。

実行モード:

| モード | 外部操作 |
| --- | --- |
| `preflight` | E2E Worker の health とマスク済み status を読む。既定値であり、deploy と外部 CRUD は行わない |
| `deploy-and-crud-smoke` | 専用 Worker を deploy し、Google、Discord、Notion の自己 cleanup 型 CRUD probe と所有状態確認を順に行う |
| `deploy-and-google-notion-smoke` | 専用 Worker を deploy し、Google event を既存の適用処理で Notion 内部 DB へ反映して検証後、両資源を cleanup する |

書き込みモードは、各 `seed_fixture` または `trigger_sync` の監査開始記録がある service / scenario だけを run ID 付きで cleanup する。実行 CLI 内の cleanup に加え、workflow の `always()` step でも一時失敗を最大3回再試行する。所有権不一致、旧 manifest、対象 fingerprint 不一致は再試行せず、他 run の資源を削除しない。

実行前に `e2e` Environment に Secret 5件が設定済みで、variableが0件であることを値を表示せず確認する。Worker URLとそのfingerprintもActionsログでマスクするためSecretとして扱う。設定helperは同名の旧variableがあればSecret登録後に削除する。Google、Discord、Notion の実行時 Secret は Cloudflare Worker だけに保持し、GitHub Actions へ複製しない。

artifact は JUnit XML、マスク済み MCP 監査要約、run manifest だけを14日保持する。run manifest の Worker URL、version、watch、外部資源は SHA-256 fingerprint または真偽値であり、生の識別子、token、request / response 本文を保存しない。

Google→Notion モードは、専用 Calendar に一意な event を作成して読み戻し、現行の `apply_google_events` へ渡し、専用 Notion 内部 DB に作られた page の内容を確認する。外部 Notion DB が空、`DISCORD_SYNC_ENABLED=false`、既定の Notion プロパティ名であることを事前に強制し、適用処理には一時状態を渡すため、同期対応表と再試行キューを KV へ保存しない。Google 認証 token の取得・更新に伴う認証 cache はこの制限の対象外である。

MCP の `trigger_sync` は `/sync/all` ではなく、所有資源を Google event と Notion page に限定した `/admin/e2e/google-notion-sync` を呼ぶ。これにより確認できるのは Google の作成・読取からアプリケーション適用処理を経た Notion 作成までであり、Google 差分取得、同期 cursor / queue、Discord 反映、実 webhook / Cron 配信、Playwright によるブラウザ表示は保証しない。

`trigger_webhook`、`trigger_job` と通常の同期・ジョブ route は、下流資源と状態を run ID で所有・回収できるまで実行しない。E2E Worker は `E2E_ORCHESTRATED_WRITES_ENABLED=false` で該当 route を `404` にし、preflight はこの既定拒否と、Google→Notion 専用 route の有効状態を別々に確認する。残作業は [GitHub Issue #17](https://github.com/lycanthr0pes/IE_Event_Bot_fork/issues/17) で追跡する。

## テスト構成

| ファイル | 対象 |
| --- | --- |
| `tests/conftest.py` | `workers` ランタイムの最小代替と外部通信の遮断 |
| `tests/fakes.py` | HTTP Request、Workers KV、Durable Object storage / namespace |
| `tests/test_entry.py` | HTTP 認可の fail-closed、Webhook token、クールダウン、排他、Webhook 重複抑止 |
| `tests/test_google_watch.py` | channel token の必須・最大長・登録、旧 watch と token 変更時の更新、外部エラー本文の非公開 |
| `tests/test_state.py` | KV 読み書き、重複抑止、Durable Object 優先経路 |
| `tests/test_sync_lock_do.py` | ロック競合・解放、Webhook 重複レコードの期限 |
| `tests/test_sync_queues.py` | Google / Discord 同期の件数制限、失敗と残件の繰り越し |
| `tests/test_e2e_entry.py` | E2E route allowlist、run ID、status のマスキング、Cron 無効化 |
| `tests/test_e2e_*_probe.py` | 外部通信を差し替えた CRUD / Google→Notion 適用、DO manifest、cleanup、応答喪失、rate limit |
| `tools/e2e_mcp_server.test.mjs` | MCP tool allowlist、接続先 fingerprint、承認、skip 判定、run manifest |
| `tools/run_e2e_workflow.test.mjs` | workflow 順序、途中失敗時 cleanup、再試行、監査対象、evidence の固定エラー |

## 外部通信の扱い

`tests/conftest.py` が Cloudflare Python Workers の `fetch` を、常にテスト失敗にする関数へ置き換える。外部 API を扱うテストは、対象モジュールの通信境界を `monkeypatch` で明示的に差し替える。

テストへ実トークン、サービスアカウント、実 DB ID、実チャンネル ID を渡してはならない。fixture には `test-token` など用途が明らかなダミー値を使う。

## 非同期コードの扱い

追加依存を増やさず、各テストは `asyncio.run()` で非同期処理を実行する。テスト内でイベントループを共有する必要が生じた場合だけ、非同期テスト用依存の追加を検討する。

## このテストで保証しないこと

ローカルテストは次を確認しない。

- Cloudflare の Python Workers、Workers KV、Durable Objects の実ランタイム互換性
- `workers/wrangler.jsonc` と Cloudflare 管理画面側バインディングの一致
- Discord、Google、Notion の現在の API 仕様、権限、レート制限、データ内容
- Cron、Google watch、Webhook の実配信
- デプロイ後の疎通、性能、可用性

これらはローカル単体テストと分け、認証情報と実行許可を確認したうえで preview または実環境の検証として扱う。

## テスト追加時の方針

- 1テストにつき、判定したい振る舞いを1つに絞る。
- 時刻、UUID、外部 API 応答は必要な境界で固定する。
- 成功だけでなく、失敗、再試行、件数上限、空データを確認する。
- 本番コードの内部実装ではなく、返り値と保存状態を優先して検証する。
- テスト後に `ruff check .`、`pyright`、`git diff --check` も実行する。
