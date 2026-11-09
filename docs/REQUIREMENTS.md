# 開発・実行要件

## 目的

この文書は、IE Event Bot を開発・検証・運用するために必要な実行環境、依存関係、外部サービスをまとめる。バージョンの正本は、各設定ファイルとする。

## 実行環境

- 開発環境: Linux / WSL
- Python: `3.10` 以上
- CI の Python: `3.12`
- Node.js: `.node-version`で指定する`22.23.4`（最低要件は`package.json`の`22.0.0`以上）
- npm: `package.json`の`packageManager`で指定する`10.9.9`（最低要件は`10.0.0`以上）
- アプリケーション実行基盤: Cloudflare Python Workers
- Worker の互換日付: `workers/wrangler.jsonc` の `compatibility_date`
- Python 仮想環境: リポジトリルートの `.venv`
- 実サービスE2E: Workers Paidを使用する。`workers/wrangler.e2e.jsonc`はCPU上限を30秒へ明示する。FreeのHTTP CPU 10msでは、複数サービス同期のCPU中断を実測した（2026-11-09）。

Windows 専用の環境は必須要件ではない。

## Python 依存関係

この節は、リポジトリ用の `.venv` に導入するPythonパッケージをまとめる。Node.jsの依存はnpmで別に管理する。

`pyproject.toml` の開発依存:

| パッケージ | バージョン | 用途 |
| --- | --- | --- |
| `ruff` | `0.15.10` | Lint |
| `pyright` | `1.1.411` | 型検査 |
| `pytest` | `9.0.3` | テスト実行 |

`workers/requirements.txt` の Worker 追加依存:

| パッケージ | バージョン | 用途 |
| --- | --- | --- |
| `rsa` | `4.9.1` | Google サービスアカウント署名 |
| `google-auth` | `2.38.0` | Google 認証 |

`.[dev]` で導入するのは開発依存の `ruff`、`pytest`、`pyright`。Worker実行用の追加依存 `rsa` と `google-auth` は `workers/requirements.txt` から導入する。依存関係を変更した場合は、設定ファイルとこの文書の整合を確認する。

## Node.js 依存関係

Node.jsとnpmの本体は、WSL共通環境へ一度導入する。`.venv/node`やリポジトリ内に本体を置かず、Python仮想環境とは独立して使用する。`.node-version`と`package.json`は使用版を宣言するファイルであり、本体のインストールは行わない。CIは`actions/setup-node`で`.node-version`を参照する。

Node.js 開発依存:

| パッケージ | バージョン | 用途 |
| --- | --- | --- |
| `wrangler` | `4.127.1` | Worker 設定検証、開発、デプロイ |
| `@modelcontextprotocol/sdk` | `1.30.0` | E2E用MCPサーバー |
| `@playwright/mcp` | `0.0.80` | ブラウザー検証用MCPサーバー |
| `zod` | `4.5.4` | MCPツールの入力検証 |

依存パッケージの正本は`package.json`と`package-lock.json`であり、`npm ci`でリポジトリの`node_modules/`へ導入する。Wranglerのグローバル版は前提にしない。

## Cloudflare バインディング

`workers/wrangler.jsonc` で確認できる必須構成:

- Workers KV: `STATE_KV`
- Durable Object: `SYNC_COORDINATOR`
- Durable Object クラス: `SyncCoordinator`
- Cron: 既定では5分間隔

`new_sqlite_classes` は Durable Object のマイグレーション指定であり、Cloudflare D1 の設定ではない。現行設定に `d1_databases` はない。

## 外部サービス

- Discord REST API
- Google Calendar API
- Notion API
- GitHub Actions、Release Please、GitHub Release

実行時には対象サービスのアカウント、API 権限、ID、トークンが別途必要である。リポジトリ内の静的検査だけでは、Cloudflare 管理画面や外部サービス側の設定完了を確認できない。

## シークレットと主要設定

最低限の本番シークレット:

- `INTERNAL_API_TOKEN`
- `GCAL_WEBHOOK_TOKEN`
- `NOTION_TOKEN`
- `DISCORD_TOKEN`
- Google 認証に使用するシークレット

主要な非シークレット設定:

- `GOOGLE_CALENDAR_ID`
- `GCAL_WEBHOOK_URL`
- `NOTION_EVENT_INTERNAL_ID`
- `DISCORD_GUILD_ID`

全設定は `README.md`、`workers/wrangler.jsonc`、`docs/SECURITY.md` を参照する。値を文書へ複製するより、正本となる設定を確認する。

## ローカルセットアップ

### WSL共通のNode.js・npmを確認する

WSLへ`.node-version`で指定したNode.jsと、同梱のnpmを導入しておく。この端末では公式Linux x64配布物を`/opt/nodejs/`へ配置し、`/usr/local/bin/node`・`npm`・`npx`から実行する。導入・更新はWSL側の管理作業であり、リポジトリのセットアップでは繰り返さない。

```bash
node --version
npm --version
node -p 'process.execPath'
```

`node`・`npm`が見つからない場合はWSL側の導入・PATHを直してから進む。Pythonの`nodeenv`で代替のNode.jsをリポジトリ内へ導入しない。PyrightもPATH上の共通Node.jsを使う。

### 仮想環境を有効化する場合

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip install -r workers/requirements.txt
npm ci
```

### 仮想環境の実行ファイルを直接使う場合

`activate` を使わず、`.venv/bin/python` / `.venv/bin/pip` を直接実行しても、同じ仮想環境を使用できる。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install -r workers/requirements.txt
npm ci
```

## 検証要件

```bash
source .venv/bin/activate
python - <<'PY'
import rsa
import google.auth
print("imports ok")
PY
ruff check .
pyright
npm run wrangler -- --version
npm run wrangler -- deploy --dry-run --config workers/wrangler.jsonc
git diff --check
```

`pytest -q` も実行する。テストは外部通信を遮断した CPython 上の単体テストであり、実際の Cloudflare ランタイムや外部サービスの疎通は保証しない。詳細は [`TESTING.md`](TESTING.md) を参照する。

## 実環境で確認する要件

- Cloudflare、Discord、Google、Notion の本番設定値と権限は、ローカルファイルだけでは確定できない。
- 実デプロイや外部 API 疎通は、認証情報と明示的な実行許可がある場合だけ検証する。
