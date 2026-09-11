# 参照資料

## 使い方

実装判断では、まずリポジトリ内のコードと設定を確認する。外部資料は API や実行基盤の仕様確認に使い、現在のコードと異なる内容をそのまま適用しない。

## リポジトリ内の正本

| 対象 | 参照先 |
| --- | --- |
| 全体概要、ルート、環境変数 | `README.md` |
| エージェント指示 | `AGENTS.md` |
| 開発規則 | `docs/DEVELOPMENT.md` |
| Python バージョンと依存 | `pyproject.toml`、`workers/requirements.txt`、`README_ENV.md` |
| Node.js 要件と Wrangler | `package.json`、`package-lock.json` |
| Worker、バインディング、変数、Cron | `workers/wrangler.jsonc` |
| HTTP、認可、Cron | `workers/src/entry.py` |
| KV と Durable Object の抽象化 | `workers/src/state.py` |
| Durable Object の保存形式 | `workers/src/sync_lock_do.py` |
| Google 差分と反映 | `workers/src/google_calendar_sync.py`、`workers/src/google_apply_sync.py` |
| Discord 同期 | `workers/src/discord_notion_sync.py` |
| Google 認証と watch | `workers/src/google_auth.py`、`workers/src/google_watch.py` |
| Q&A、リマインド、クリーンアップ | `workers/src/jobs.py` |
| クラス・関数・API依存、実行フロー、信頼境界 | `docs/architecture/plantuml/architecture-index.json`、`docs/architecture/plantuml/architecture-catalog.md`、同ディレクトリの `.puml` |
| CI | `.github/workflows/ci.yml` |
| PlantUML図の検証・SVG生成 | `.github/workflows/plantuml.yml`、`tools/validate_plantuml.py` |
| Pull Request 規則 | `.github/workflows/commitlint.yml`、`.github/workflows/pr-target-guard.yml` |
| Release | `.github/workflows/release-please.yml`、`.github/workflows/sync-main-to-develop.yml` |

## リポジトリ内の詳細文書

- `docs/Event_Bot仕様書.md`: 機能と運用の詳細仕様
- `docs/KV.md`: Workers KV のキーと確認方法
- `docs/Operations.md`: デプロイと運用
- `docs/do-kv-design.md`: Durable Object と KV の責務分担
- `docs/fork-upstream-workflow.md`: Fork と Upstream を使う Git 運用

先頭4文書は現行の `.gitignore` 対象であり、ローカル補足文書として存在する。クリーンなチェックアウトで必ず存在するとは限らないため、標準文書はこれらだけに依存しない。

## 外部の一次資料

- Cloudflare Workers Python: <https://developers.cloudflare.com/workers/languages/python/>
- Cloudflare Wrangler の導入: <https://developers.cloudflare.com/workers/wrangler/install-and-update/>
- Cloudflare Workers KV: <https://developers.cloudflare.com/kv/>
- Cloudflare Durable Objects: <https://developers.cloudflare.com/durable-objects/>
- Google Calendar API: <https://developers.google.com/calendar/api/guides/overview>
- Google Calendar push notifications: <https://developers.google.com/calendar/api/guides/push>
- Google Calendar Events watch: <https://developers.google.com/workspace/calendar/api/v3/reference/events/watch>
- Notion API: <https://developers.notion.com/reference/intro>
- Discord Guild Scheduled Event: <https://discord.com/developers/docs/resources/guild-scheduled-event>
- PlantUML download: <https://plantuml.com/download>
- PlantUML layout engines: <https://plantuml.com/layout-engines>
- GitHub Actions: <https://docs.github.com/actions>
- Release Please: <https://github.com/googleapis/release-please>

外部仕様は更新されるため、挙動変更やデプロイ前には現在の公式資料を再確認する。

## 参照時の注意

- 一般名の `database_id` は、呼び出し先を確認して解釈する。このリポジトリでは Notion API のデータベース ID として使われる箇所がある。
- `new_sqlite_classes` を Cloudflare D1 と誤認しない。
- 文書の設定値が `workers/wrangler.jsonc` と異なる場合は、現行設定とコードを確認して文書を更新する。
- ローカル検査だけで、Cloudflare 管理画面、GitHub Secret、外部サービスの権限を確定しない。
