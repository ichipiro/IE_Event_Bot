# Commit, Pull Request, Merge, and Release Workflow

このリポジトリは、日常開発を `develop`、公開可能な状態を `main` で管理します。
`main` と `develop` への直接 push は行わず、Pull Request と必須チェックを経由します。

## 1. Remote の役割

- `origin`: 自分たちが管理する fork (`lycanthr0pes/IE_Event_Bot_fork`)
- `upstream`: 取り込み元 (`ichipiro/IE_Event_Bot`)

初回だけ、URLと追跡先を確認します。

```bash
git remote set-url origin https://github.com/lycanthr0pes/IE_Event_Bot_fork.git
git remote add upstream https://github.com/ichipiro/IE_Event_Bot.git
# upstream が既にある場合:
git remote set-url upstream https://github.com/ichipiro/IE_Event_Bot.git

git fetch --all --prune
git branch --set-upstream-to=origin/develop develop
```

## 2. ブランチ規則

| Head branch | PR base | 用途 |
| --- | --- | --- |
| `feature/*` | `develop` | 機能追加、修正、文書、リファクタリング |
| `release/*` | `main` | `develop` の内容をリリース候補として昇格 |
| `hotfix/*` | `main` | 本番向け緊急修正 |
| `sync/*` | `develop` | upstreamまたはリリース後の`main`を同期 |
| `release-please--*` | `main` | Release Pleaseが生成するリリースPR |

`PR Target Guard` がこの対応を検査します。

## 3. 通常の開発

作業開始前に、fork側の最新`develop`を取得します。

```bash
git fetch origin --prune
git switch develop
git pull --ff-only origin develop
git switch -c feature/<topic>
```

変更を検証してConventional Commits形式でコミットします。

```bash
.venv/bin/ruff check .
.venv/bin/pyright
.venv/bin/pytest -q

git add <変更したファイル>
git commit -m "feat: describe the change"
git push -u origin feature/<topic>
```

`feature/<topic> -> develop` のPRを作成します。PRタイトルと各コミットは、次のようなConventional Commits形式にします。

```text
feat: add webhook validation
fix: retry failed notification
docs: document the release flow
chore: update development tooling
```

レビューと必須チェックが完了したらmergeします。merge方式はGitHubのリポジトリ設定に従います。

## 4. upstreamの取り込み

upstreamの履歴は共有ブランチを直接rebaseせず、`sync/*` PRで取り込みます。

```bash
git fetch origin upstream --prune
git switch develop
git pull --ff-only origin develop
git switch -c sync/upstream-develop
git merge upstream/develop
git push -u origin sync/upstream-develop
```

競合を解決して検証した後、`sync/upstream-develop -> develop` のPRを作成します。upstream由来コミットはConventional Commits形式とは限らないため、`sync/*`ではPRタイトルだけをCommitlint対象にします。

## 5. リリース

1. 最新の`develop`から`release/x.y.z`を作成します。
2. `release/x.y.z -> main` のPRを作成します。
3. レビューと必須チェック後にmergeします。
4. `main`へのpushを契機にRelease Pleaseが`release-please--* -> main`のPRを作成または更新します。
5. Release Please PRをmergeすると、再度`main`のworkflowが動き、`vX.Y.Z`タグとGitHub Releaseを作成します。
6. `v*`タグを契機に`sync/main-to-develop-vX.Y.Z -> develop`のPRが自動作成されます。
7. 同期PRのチェックと差分を確認してmergeします。

```bash
git fetch origin --prune
git switch develop
git pull --ff-only origin develop
git switch -c release/x.y.z
git push -u origin release/x.y.z
```

バージョンはConventional CommitsからRelease Pleaseが決定します。ブランチ名の`x.y.z`はリリース予定を表しますが、最終的なタグはRelease Please PRで確認します。

## 6. Hotfix

hotfixは最新の`main`から作成します。

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git switch -c hotfix/<topic>
```

`hotfix/<topic> -> main`をmergeするとRelease PleaseがパッチリリースPRを作成します。その後は通常リリースと同様に、タグ作成と`main -> develop`同期PRまで進めます。

## 7. GitHubリポジトリの必須設定

Repository Secret `RELEASE_AUTOMATION_TOKEN`を登録します。Release Pleaseと自動同期PRの両方がこのトークンを使用します。

トークンには対象リポジトリに対する次の権限が必要です。

- Contents: read and write
- Pull requests: read and write

通常の`GITHUB_TOKEN`で作成したPRは後続workflowを起動しないため、必須チェックを動かすには専用トークンが必要です。

GitHubのbranch protection/rulesetでは、`main`と`develop`について次を設定します。

- Pull Request経由の変更を必須にする
- `CI / test`、`Commitlint / lint`、`PR Target Guard / guard`を必須チェックにする
- 必要なレビュー数を設定する
- 管理者を含め直接pushを禁止する

## 8. Merge後の確認

```bash
git fetch origin --prune --tags
git log --oneline --decorate -10 origin/main origin/develop
git status --short --branch
```

このリポジトリにはCloudflareへの自動デプロイworkflowはありません。Gitのmerge、GitHub Release、Cloudflare Workerのデプロイは別の工程です。
