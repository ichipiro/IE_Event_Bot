# エージェント作業指示

## 目的

- この文書は、このリポジトリで作業する AI アシスタントが最初に読む案内である。
- 安全で最小限かつ検証可能な変更を優先する。
- 推測より、リポジトリ内で確認できるコード、設定、文書を優先する。
- 人間向けの説明、コメント、文書は原則として日本語で記述する。

## 指示の優先順位

このリポジトリでは、以下の順序で指示を適用する。

1. `AGENTS.md`
2. `docs/DEVELOPMENT.md`
3. その他のプロジェクト文書
4. 既存コードから推測される慣習

上位の文書と下位の文書が競合する場合は、上位を優先する。
実装、修正、リファクタリング、テスト、コードレビュー、コミットメッセージ作成の前に、`docs/DEVELOPMENT.md` を読む。
既存コードの慣習だけを理由に、`docs/DEVELOPMENT.md` の規則を無視してはならない。

## ワークスペース境界

- ユーザーから明確な指示がない限り、WSL ワークスペース外のファイルへアクセスしない。
- WSL 外へのアクセスをユーザーが依頼した場合でも、実行前に対象を示して確認を得る。
- Windows 側のパス、マウントされたドライブ、現在の Linux ワークスペース外のホームディレクトリ、クラウド同期フォルダー、GUI で開かれたファイルは、依頼と確認の両方がない限り対象外とする。
- このリポジトリ内で完結できる作業は、`/home/products/Git_Products/IE/IE_Event_Bot_fork` の外へ広げない。
- `workers/service-account.json`、`.dev.vars*`、トークン、秘密鍵などの機密情報を読み取ったり、出力したり、コミットしたりしない。

## 実行環境

- 主な実行環境は Linux / WSL である。
- Python 作業にはリポジトリルートの `.venv` を使う。
- Windows 専用の手順は、ユーザーが明示的に求めた場合だけ提示する。
- 主なアプリケーション対象は `workers/` 配下の Cloudflare Python Workers である。
- Cloudflare、Discord、Google、Notion の実環境状態は、ローカルの静的検査だけでは確定できない。

## セットアップ

既存の Linux 仮想環境を有効化する。

```bash
source .venv/bin/activate
```

仮想環境がない場合は作成する。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip install -r workers/requirements.txt
```

`README_ENV.md` に記載された `.venv/bin/python` などの直接実行も同じ仮想環境を使用する方法として扱う。

## プロジェクト構成

- `workers/src/entry.py`: Worker の入口、HTTP ルーティング、Cron、同期ディスパッチ。
- `workers/src/google_calendar_sync.py`: Google カレンダーの差分取得。
- `workers/src/google_apply_sync.py`: Google の変更を Notion と Discord へ反映。
- `workers/src/discord_notion_sync.py`: Discord から Notion および Google への同期。
- `workers/src/google_auth.py`: Google トークンの解決とキャッシュ。
- `workers/src/google_watch.py`: Google watch の登録、更新、維持。
- `workers/src/jobs.py`: Q&A、リマインド、クリーンアップの定期ジョブ。
- `workers/src/health_checks.py`: 外部サービスへの疎通確認。
- `workers/src/state.py`: Workers KV と Durable Object を介した状態管理。
- `workers/src/sync_lock_do.py`: 排他、最終同期時刻、Webhook 重複抑止を扱う Durable Object。
- `workers/wrangler.jsonc`: Cloudflare Workers のバインディング、変数、Cron 設定。
- `tests/`: 外部通信を遮断したローカル単体テストと Workers API の代替実装。
- `pyproject.toml`: Python パッケージ情報と開発依存。
- `README_ENV.md`: リポジトリの仮想環境へ導入する Python パッケージ。

## リポジトリ固有の規則

- 目的に必要な小さな変更だけを行い、無関係な整形やリファクタリングを混ぜない。
- ユーザーが明示的に変更を求めない限り、既存の振る舞いを保つ。
- ユーザーファイル、KV 関連文書、生成物を無断で削除しない。
- 開始時に未コミット変更を確認し、既存変更をユーザーの作業として保持する。
- 環境・セットアップ手順は、確認済みの Linux / WSL の挙動に合わせる。
- 依存関係を変更した場合、必要に応じて `README_ENV.md` も更新する。
- Worker の挙動を変更した場合、`workers/wrangler.jsonc` と、該当する HTTP ルートまたは Cron の入口を併せて確認する。
- 状態管理を変更した場合、Workers KV の `STATE_KV` と Durable Object の `SYNC_COORDINATOR` の責務分担を保つ。
- `new_sqlite_classes` は Durable Object のマイグレーションであり、Cloudflare D1 のバインディングではない。
- コミットと Pull Request のタイトルには、`docs/DEVELOPMENT.md` で定める Conventional Commits 形式を使う。
- 文書の記述とコードが食い違う場合は、コードと設定を現行実装として確認し、文書の不確実性を明記する。

## 検証

依存関係の基本確認:

```bash
source .venv/bin/activate
python - <<'PY'
import rsa
import google.auth
print("imports ok")
PY
```

静的検査:

```bash
source .venv/bin/activate
ruff check .
pyright
```

ローカル単体テスト:

```bash
source .venv/bin/activate
pytest -q
```

テストは外部 API をスタブ化しており、Cloudflare、Discord、Google、Notion の実環境動作を証明しない。詳細は `docs/TESTING.md` を参照する。
文書だけの変更でも、相対リンクの解決、`git diff --check`、意図しないファイル変更の有無を確認する。
実行できなかった検証は、実行済みとして報告しない。

## 既知の注意点

- このリポジトリは Windows ネイティブではなく、Linux / WSL ワークスペースとして扱う。
- Cloudflare の実行時挙動は `workers/wrangler.jsonc` のバインディングと管理画面側の状態にも依存する。
- `README_ENV.md` は、リポジトリの仮想環境に属する Python パッケージだけを扱う。
- `docs/Event_Bot仕様書.md`、`docs/KV.md`、`docs/Operations.md`、`docs/do-kv-design.md` は既存の `.gitignore` 対象である。ローカルでは保持し、標準文書には必要な内容を統合するが、追跡方針は別の明示的な変更なしに変えない。
- Git のマージ、GitHub Release、Cloudflare Worker のデプロイは別々の工程である。

# Markdown目次

## 入口

- [`README.md`](README.md)
  - リポジトリの概要、機能、設定、初期確認。
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
  - 開発ルール、変更方針、コミット規則。

## 設計・要件

- [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md)
  - 実行環境、依存パッケージ、外部サービス、検証要件。
- [`docs/FRONTEND.md`](docs/FRONTEND.md)
  - フロントエンドの有無と HTTP インターフェースの境界。
- [`docs/BACKEND.md`](docs/BACKEND.md)
  - Cloudflare Python Workers の構成と同期フロー。
- [`docs/SECURITY.md`](docs/SECURITY.md)
  - 認証、シークレット、外部 API、運用上の安全対策。
- [`docs/DB-SCHEMA.md`](docs/DB-SCHEMA.md)
  - Notion、Workers KV、Durable Object のデータ設計。
- [`docs/REFERENCES.md`](docs/REFERENCES.md)
  - 実装根拠となるリポジトリ内資料と外部仕様。

## 詳細資料

- [`README_ENV.md`](README_ENV.md)
  - Linux 仮想環境に導入する Python パッケージ。
- [`docs/TESTING.md`](docs/TESTING.md)
  - 外部通信を遮断したローカル単体テストの構成、実行方法、検証境界。
- [`docs/Event_Bot仕様書.md`](docs/Event_Bot仕様書.md)
  - 追跡対象外で維持するローカル機能仕様書。
- [`docs/KV.md`](docs/KV.md)
  - 追跡対象外で維持する Workers KV のローカル補助。
- [`docs/Operations.md`](docs/Operations.md)
  - 追跡対象外で維持する運用上のローカル補助。
- [`docs/do-kv-design.md`](docs/do-kv-design.md)
  - 追跡対象外で維持する状態設計のローカル補助。
- [`docs/fork-upstream-workflow.md`](docs/fork-upstream-workflow.md)
  - Fork、Upstream、Pull Request、Release、同期の運用。
- [`CHANGELOG.md`](CHANGELOG.md)
  - Release Please が更新する公開リリース履歴。

## 作業管理

- [`docs/ISSUES.md`](docs/ISSUES.md)
  - コードと設定から確認できる未解決事項。
- [`docs/GOAL.md`](docs/GOAL.md)
  - プロジェクトの目標と完了条件。
- [`docs/WORKLOG.md`](docs/WORKLOG.md)
  - 文書化を含む作業履歴。
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md)
  - プロジェクト文書の変更履歴。
