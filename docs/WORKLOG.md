# 作業履歴

## 記録方針

- 目的、変更した文書または機能、実施した検証、未確認事項を簡潔に記録する。
- Git のコミット履歴を置き換えず、作業の判断と検証境界を補足する。
- シークレット、個人情報、外部サービスの認証値を記録しない。

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
