# 作業履歴

## 記録方針

- 目的、変更した文書または機能、実施した検証、未確認事項を簡潔に記録する。
- Git のコミット履歴を置き換えず、作業の判断と検証境界を補足する。
- シークレット、個人情報、外部サービスの認証値を記録しない。

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
