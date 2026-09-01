# 文書変更履歴

この文書は、標準文書構成の変更を記録する。アプリケーションの公開リリース履歴は、Release Please が管理するリポジトリルートの `CHANGELOG.md` を正本とする。

## 2026-08-29

### 追加

- 外部通信を遮断するローカル単体テスト基盤と主要同期制御のテスト。
- `docs/TESTING.md`。
- Google Webhook channel token の登録・照合と、token 変更時の watch 再登録。
- `package.json` と `package-lock.json` による Wrangler の版固定。
- `docs/DEVELOPMENT.md`
- `docs/REQUIREMENTS.md`
- `docs/FRONTEND.md`
- `docs/BACKEND.md`
- `docs/SECURITY.md`
- `docs/DB-SCHEMA.md`
- `docs/REFERENCES.md`
- `docs/ISSUES.md`
- `docs/GOAL.md`
- `docs/WORKLOG.md`
- `docs/CHANGELOG.md`

### 変更

- CI で `pytest -q` を常時実行するように変更。
- 内部 API と Google Webhook の認証を、Secret 未設定時も処理を開始しない fail-closed へ変更。
- Google watch API の外部エラー本文を管理応答と状態履歴へ流さないように変更。
- `AGENTS.md` をテンプレートの指示優先順位と Markdown 目次へ統合し、日本語化。
- 既存の WSL 境界、Linux 仮想環境、Cloudflare Workers、依存関係、検証規則を保持。
- `docs/do-kv-design.md` の英語本文を日本語化。
- `docs/Operations.md` と `docs/KV.md` のコマンド例を WSL / Linux 向けに統合。

### 保持

- 既存の README、仕様、KV、運用、Fork / Upstream 文書。
- 作業開始前から存在した未コミット変更。
- `docs/Event_Bot仕様書.md`、`docs/KV.md`、`docs/Operations.md`、`docs/do-kv-design.md` を追跡対象外のローカル補助とする方針。
