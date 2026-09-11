# Fork、Upstream、Pull Request、Release、同期の運用フロー

## 1. 目的

この文書は、ローカルで作成した変更をforkへpushし、forkからupstreamへPull Requestを送り、upstreamでリリースした後にforkへ同期し直すまでの正規フローを定義します。

| Remote名 | GitHubリポジトリ | 役割 |
| --- | --- | --- |
| `origin` | `lycanthr0pes/IE_Event_Bot_fork` | 自分が管理するfork。作業ブランチのpush先とupstream同期先 |
| `upstream` | `ichipiro/IE_Event_Bot` | 正式な統合・リリース元 |

GitHubの既定ブランチは、originでは`develop`、upstreamでは`main`とする。originの手動E2E workflowは`develop`にあり、`workflow_dispatch`へ登録するためにもoriginの既定ブランチを`develop`に維持する。

upstreamを正式な`develop`、`main`、タグ、GitHub Releaseの権威とします。originで独立したリリースを同時に進めると、同じ変更に対して別のタグ、Release Please PR、merge履歴が作られるため、両方のリポジトリをリリース元として扱いません。

## 2. 操作場所の表記

各工程には、操作が行われる場所を次のラベルで明記します。

| ラベル | 意味 |
| --- | --- |
| **ローカル** | 手元のcheckoutだけを変更する。GitHub上のブランチは変更しない |
| **origin側** | `lycanthr0pes/IE_Event_Bot_fork`へpush、PR作成、mergeを行う |
| **upstream側** | `ichipiro/IE_Event_Bot`へpush、PR作成、merge、Release作成を行う |
| **自動** | GitHub Actionsがイベントを受けて処理する |
| **手動** | 開発者またはmaintainerが明示的に操作する |

`git merge upstream/develop`は、ローカルに保存されたremote-tracking refを現在のローカルブランチへmergeするコマンドです。名前に`upstream`を含みますが、upstream側を変更しません。

## 3. ブランチとPRの対応

### 3.1 upstreamへ変更を送るPR

| Head | Base | 目的 |
| --- | --- | --- |
| `origin:feature/*` | `upstream:develop` | 機能追加、修正、文書、リファクタリング |

originの`feature/*`を先に`origin/develop`へmergeする必要はありません。upstreamへの貢献は、originのfeatureブランチからupstreamの`develop`へ直接cross-repository PRを作成します。

### 3.2 upstream内部のPR

| Head | Base | 目的 |
| --- | --- | --- |
| `upstream:release/*` | `upstream:main` | `develop`の内容を正式リリースへ昇格 |
| `upstream:hotfix/*` | `upstream:main` | 本番向け緊急修正 |
| `upstream:release-please--*` | `upstream:main` | Release Pleaseによるバージョン・CHANGELOG更新 |
| `upstream:sync/main-to-develop-*` | `upstream:develop` | リリース後の`main`を`develop`へ戻す |

### 3.3 upstreamからoriginへ戻すPR

| Head | Base | 目的 |
| --- | --- | --- |
| `origin:sync/upstream-develop-*` | `origin:develop` | 完成した`upstream/develop`をforkへ同期 |

現在定義されている逆同期の最終到達点は`origin/develop`です。`upstream/main`を`origin/main`へ同期する工程は、現行のPR Target Guardとorigin側Release Pleaseの責務が衝突するため、未定義です。

## 4. 全体フロー

```text
【ローカル】
feature/*を作成・commit
        │ [手動] git push origin
        ▼
【origin側】origin:feature/*
        │ [手動] cross-repository PR
        ▼
════════════ origin / upstream 境界 ════════════
        ▼
【upstream側】feature PR → upstream:develop
        │ [自動] PR checks
        │ [手動] review・merge
        ▼
upstream:develop
        │ [手動] release/*作成・PR
        │ [自動] PR checks
        │ [手動] merge
        ▼
upstream:main
        │ [自動] Release Please PR作成
        │ [手動] Release Please PR merge
        │ [自動] vX.Y.Zタグ・GitHub Release作成
        ▼
upstream:main
        │ [自動] sync/main-to-develop-vX.Y.Z PR作成
        │ [手動] review・merge
        ▼
upstream:develop
        │ [手動] fetch・ローカルmerge・originへpush
        ▼
════════════ upstream / origin 境界 ════════════
        ▼
【origin側】origin:sync/upstream-develop-vX.Y.Z
        │ [自動] PR checks
        │ [手動] review・merge
        ▼
origin:develop
```

## 5. 初期設定と事前確認

### 5.1 Remote URLを確認する【ローカル・手動】

```bash
git remote -v
```

期待値:

```text
origin    https://github.com/lycanthr0pes/IE_Event_Bot_fork.git (fetch)
origin    https://github.com/lycanthr0pes/IE_Event_Bot_fork.git (push)
upstream  https://github.com/ichipiro/IE_Event_Bot.git (fetch)
upstream  https://github.com/ichipiro/IE_Event_Bot.git (push)
```

必要な場合だけURLを設定します。

```bash
git remote set-url origin https://github.com/lycanthr0pes/IE_Event_Bot_fork.git
git remote set-url upstream https://github.com/ichipiro/IE_Event_Bot.git
```

`upstream`が存在しない場合:

```bash
git remote add upstream https://github.com/ichipiro/IE_Event_Bot.git
```

### 5.2 正しいfetch構文【ローカル・手動】

```bash
git fetch --prune --multiple origin upstream
```

または個別に取得します。

```bash
git fetch origin --prune
git fetch upstream --prune
```

次の構文は誤りです。

```bash
git fetch origin upstream --prune
```

これは`origin`から`upstream`というrefを取得しようとするため、次のエラーになります。

```text
fatal: couldn't find remote ref upstream
```

### 5.3 作業ツリーをcleanにする【ローカル・手動】

```bash
git status --short --branch
```

別作業の未コミット変更をfeature、release、syncブランチへ混入させません。変更は本来の作業ブランチでcommitするか、意図を明確にして退避してから次の工程へ進みます。

## 6. featureブランチをoriginへpushする

### 6.1 作業開始【ローカル・手動】

originの最新`develop`からfeatureブランチを作ります。

```bash
git fetch origin --prune
git switch develop
git pull --ff-only origin develop
git switch -c feature/<topic>
```

`--ff-only`は、最新化の段階で意図しないmerge commitを作らないための指定です。

### 6.2 変更・検証・commit【ローカル・手動】

```bash
.venv/bin/ruff check .
.venv/bin/pyright
.venv/bin/pytest -q
git diff --check
```

変更をConventional Commits形式でcommitします。

```bash
git add <変更したファイル>
git commit -m "feat: describe the change"
```

例:

```text
feat: add webhook validation
fix: retry failed notification
docs: document the fork workflow
chore: update development tooling
```

### 6.3 originへpush【origin側・手動】

```bash
git push -u origin feature/<topic>
```

この時点で変更されるのは`origin:feature/<topic>`だけです。`origin/develop`、`origin/main`、upstreamの全ブランチは変更されません。

## 7. originからupstream/developへmergeする

### 7.1 Cross-repository PR作成【origin → upstream・手動】

```bash
gh pr create \
  --repo ichipiro/IE_Event_Bot \
  --base develop \
  --head lycanthr0pes:feature/<topic> \
  --title "feat: describe the change" \
  --body "変更内容、影響範囲、検証結果を記載する。"
```

PRの向き:

```text
Head: lycanthr0pes/IE_Event_Bot_fork:feature/<topic>
Base: ichipiro/IE_Event_Bot:develop
```

このPRがoriginとupstreamの書き込み境界です。PR作成後のレビュー、checks、mergeはupstream側で行われます。

### 7.2 PR checks【upstream側・自動】

`pull_request`イベントはbaseリポジトリであるupstream側へ配送されます。修正版workflowがupstreamのbase branchへ反映された後は、次を実行します。

- `CI / test`: Ruff、Pyright、テスト検出とテスト実行
- `Commitlint / lint`: PRタイトルと対象コミットのConventional Commits検査
- `PR Target Guard / guard`: `feature/* → develop`の対応検査

origin側のActionsが不調でも、cross-repository PRのchecksはupstream側で生成されます。upstreamでは過去に`origin:feature/v1 → upstream:develop`の成功実績があります。

2026-08-29時点の`upstream/develop`には旧版CIがあるため、`feature/v3`を送る最初のPRではRuffとテスト検出は実行されますが、Pyrightはまだ実行されません。修正版CIがmergeされた後のPRからPyrightも対象になります。

### 7.3 レビュー修正【ローカル・origin側・手動】

```bash
git switch feature/<topic>
git add <修正したファイル>
git commit -m "fix: address review comments"
git push origin feature/<topic>
```

既存PRは自動的に更新され、upstream側checksが再実行されます。PRを作り直しません。

### 7.4 upstream/developへmerge【upstream側・手動】

```bash
gh pr merge <PR番号> \
  --repo ichipiro/IE_Event_Bot \
  --merge
```

upstreamの履歴とoriginから来たコミットの祖先関係を保つため、cross-repository PRではmerge commit方式を推奨します。squash mergeでは内容が統合されてもGitの祖先関係が残らず、後の逆同期で同じコミットが未同期に見えることがあります。

## 8. upstream/developをupstream/mainへ昇格する

この工程からRelease完了まではupstream側maintainerの責任です。origin側でreleaseブランチをmergeしてもupstreamは更新されません。

### 8.1 releaseブランチ作成【ローカル・手動】

```bash
git fetch upstream --prune
git switch -c release/x.y.z upstream/develop
```

ブランチ名の`x.y.z`はリリース予定を表します。最終バージョンはRelease Please PRで確認します。

### 8.2 upstreamへreleaseブランチをpush【upstream側・手動】

```bash
git push -u upstream release/x.y.z
```

この操作にはupstreamへのwrite権限が必要です。権限がない開発者はここから先をupstream maintainerへ引き継ぎます。

### 8.3 upstream/main向けPR作成【upstream側・手動】

```bash
gh pr create \
  --repo ichipiro/IE_Event_Bot \
  --base main \
  --head release/x.y.z \
  --title "chore(release): promote develop to main" \
  --body "developのリリース候補をmainへ昇格する。"
```

PRの向き:

```text
upstream:release/x.y.z → upstream:main
```

### 8.4 release PR checks【upstream側・自動】

- CI
- Commitlint
- PR Target Guardによる`release/* → main`検査

### 8.5 upstream/mainへmerge【upstream側・手動】

```bash
gh pr merge <PR番号> \
  --repo ichipiro/IE_Event_Bot \
  --merge
```

`develop`が`main`の祖先として残るよう、release PRでもmerge commit方式を推奨します。

## 9. Release PleaseによるタグとGitHub Release

### 9.1 必須Secret【upstream側・手動設定】

upstreamリポジトリにRepository Secret `RELEASE_AUTOMATION_TOKEN`を登録します。

Fine-grained Personal Access Tokenには、upstreamリポジトリに対して次の権限が必要です。

- Contents: read and write
- Pull requests: read and write

通常の`GITHUB_TOKEN`で自動作成したPRは後続workflowを起動しないため、Release Please PRと同期PRには専用トークンを使用します。トークン値をログ、文書、commitへ出力しません。

### 9.2 Release Please PR作成【upstream側・自動】

`release/* → upstream:main`がmergeされ、`main`へpushイベントが発生すると、Release Pleaseが次を実行します。

1. Conventional Commitsから次バージョンを計算する。
2. CHANGELOGとmanifestを更新する。
3. `release-please--* → upstream:main`のPRを作成または更新する。

### 9.3 Release Please PR確認・merge【upstream側・手動】

確認対象:

- バージョン番号
- CHANGELOG
- `.release-please-config.json`
- `release-please-manifest.json`
- 意図しない変更が含まれていないこと

確認後、Release Please PRをmergeします。

### 9.4 タグ・GitHub Release作成【upstream側・自動】

Release Please PRのmergeによる`main`へのpushで、Release Pleaseが再実行されます。次が作成されます。

- `vX.Y.Z`タグ
- upstreamのGitHub Release

Cloudflare Workerのデプロイはこのworkflowに含まれません。GitHub ReleaseとCloudflareデプロイは別工程です。

## 10. upstream/mainをupstream/developへ同期する

### 10.1 同期PR作成【upstream側・自動】

`vX.Y.Z`タグのpushを契機に、同期workflowが次を実行します。

1. `upstream:main`から`sync/main-to-develop-vX.Y.Z`を作成する。
2. 同期ブランチをupstreamへpushする。
3. `sync/main-to-develop-vX.Y.Z → upstream:develop`のPRを作成する。

戻す対象:

- Release Pleaseによるバージョン更新
- CHANGELOG
- manifest
- hotfixなどの`main`固有変更

### 10.2 同期PR checks【upstream側・自動】

- CI
- Commitlint
- PR Target Guardによる`sync/* → develop`検査

upstream由来または自動生成コミットがConventional Commits形式とは限らないため、修正版Commitlintでは`sync/*`の各コミット検査を省略し、PRタイトルだけを検査します。

### 10.3 upstream/developへmerge【upstream側・手動】

```bash
gh pr merge <PR番号> \
  --repo ichipiro/IE_Event_Bot \
  --merge \
  --delete-branch
```

同期PRをsquashすると`upstream:main`の祖先関係が`upstream:develop`へ残りません。次回同期時の重複や競合を避けるため、merge commit方式を使用します。

このmergeが終わるまで、originへの逆同期を開始しません。

## 11. upstream/developをorigin/developへ同期する

この章ではupstreamは読み取り専用です。ブランチ、PR、mergeを作る場所はorigin側です。

### 11.1 前提確認【upstream側完了・手動確認】

開始条件:

- upstreamのrelease PRが`upstream:main`へmerge済み
- Release PleaseによるタグとGitHub Releaseが作成済み
- `upstream:main → upstream:develop`同期PRがmerge済み

### 11.2 作業ツリー確認【ローカル・手動】

```bash
git status --short --branch
```

別作業の未コミット変更がある場合は先に本来のブランチでcommitします。同期ブランチへ混入させません。

### 11.3 origin・upstreamを取得【ローカル・両remote読み取り】

```bash
git fetch --prune --multiple origin upstream
```

GitHub上のブランチは変更されません。ローカルの`origin/develop`と`upstream/develop`だけが更新されます。

### 11.4 同期要否を確認【ローカル・手動】

```bash
git rev-list --left-right --count \
  upstream/develop...origin/develop
```

出力順:

```text
upstreamだけのコミット数    originだけのコミット数
```

| 出力例 | 状態 |
| --- | --- |
| `0 0` | 完全一致。同期不要 |
| `3 0` | upstreamが3コミット先行 |
| `0 2` | originだけが2コミット先行 |
| `3 2` | 両方が分岐。mergeが必要 |

取り込むコミットと差分を確認します。

```bash
git log --oneline origin/develop..upstream/develop
git diff --stat origin/develop...upstream/develop
```

### 11.5 ローカルdevelopをoriginに合わせる【ローカル・origin読み取り】

```bash
git switch develop
git pull --ff-only origin develop
```

`fatal: Not possible to fast-forward`になった場合、ローカル`develop`にorigin未反映コミットがあります。resetせず、次で確認します。

```bash
git log --oneline --left-right origin/develop...develop
```

### 11.6 同期ブランチ作成【ローカル・手動】

リリースまたは同期単位が分かる一意な名前を使用します。

```bash
git switch -c sync/upstream-develop-vX.Y.Z
```

この時点では同期ブランチはローカルにしか存在しません。

### 11.7 upstream/developをローカル同期ブランチへmerge【ローカル・手動】

```bash
git merge --no-edit upstream/develop
```

結果の読み方:

- `Already up to date.`: 同期済み。PRは不要
- `Fast-forward`: origin固有の分岐がなく、upstreamまで進んだ
- `Merge made by the 'ort' strategy.`: 両方の履歴をmerge commitで統合した
- `CONFLICT`: 手動解決が必要

競合確認:

```bash
git status
git diff --name-only --diff-filter=U
```

競合解決後:

```bash
git add <解決したファイル>
git commit
```

同期を中止する場合:

```bash
git merge --abort
```

共有ブランチをrebaseしたり、`origin/develop`をresetしたりしません。

### 11.8 差分と品質を検証【ローカル・手動】

```bash
git log --oneline origin/develop..HEAD
git diff --stat origin/develop...HEAD
git diff origin/develop...HEAD
.venv/bin/ruff check .
.venv/bin/pyright
.venv/bin/pytest -q
git diff --check
```

確認事項:

- upstreamにないローカル変更が混入していない
- secretや環境ファイルを追加していない
- 競合解決で機能やworkflowを削除していない
- Ruff、Pyright、対象テストが成功している

### 11.9 同期ブランチをoriginへpush【origin側・手動】

```bash
git push -u origin sync/upstream-develop-vX.Y.Z
```

この時点で変更されるのは`origin:sync/upstream-develop-vX.Y.Z`だけです。`origin/develop`とupstreamはまだ変更されません。

### 11.10 origin内PR作成【origin側・手動】

```bash
gh pr create \
  --repo lycanthr0pes/IE_Event_Bot_fork \
  --base develop \
  --head sync/upstream-develop-vX.Y.Z \
  --title "chore(sync): upstream/develop -> origin/develop (vX.Y.Z)" \
  --body "upstream release後のdevelopをoriginへ同期する。検証結果も記載する。"
```

PRの向き:

```text
origin:sync/upstream-develop-vX.Y.Z → origin:develop
```

upstreamにはPRを作成しません。

### 11.11 origin PR checks【origin側・自動】

- CI
- Commitlint
- PR Target Guardによる`sync/* → develop`検査

確認コマンド:

```bash
gh pr checks <PR番号> \
  --repo lycanthr0pes/IE_Event_Bot_fork
```

### 11.12 origin/developへmerge【origin側・手動】

```bash
gh pr merge <PR番号> \
  --repo lycanthr0pes/IE_Event_Bot_fork \
  --merge \
  --delete-branch
```

upstreamの祖先関係をoriginへ残すため、同期PRはsquashせずmerge commit方式を使用します。

### 11.13 同期完了確認【ローカル・両remote読み取り】

```bash
git fetch --prune --multiple origin upstream
git switch develop
git pull --ff-only origin develop
git merge-base --is-ancestor upstream/develop origin/develop
echo $?
```

`0`なら、`upstream/develop`の全コミットが`origin/develop`に含まれています。

追加確認:

```bash
git rev-list --left-right --count \
  upstream/develop...origin/develop
```

期待値は`0 N`です。左が`0`ならupstream側に未同期コミットはありません。右の`N`はorigin固有コミット数で、存在しても同期漏れではありません。

## 12. origin/mainの扱い

この運用ではupstreamを正式リリース元とし、originはfeatureブランチのpush先と`develop`の同期先として扱います。

現在、`upstream/main → origin/main`の安全な同期フローは実装されていません。

理由:

1. 現行PR Target Guardは`sync/*`のbaseを`develop`に限定している。
2. `sync/upstream-main-* → origin:main`はTarget Guardに拒否される。
3. origin側Release Pleaseが有効な状態でupstreamのrelease commitを`origin/main`へ入れると、originでも重複したRelease Please処理が起動し得る。
4. originとupstreamの`main`はすでに異なるmerge履歴を持つため、単純なfast-forwardでは同期できない。

完全なmirrorが必要な場合は、実行前に次を設計・実装します。

- `sync/upstream-main-* → main`を許可するTarget Guard規則
- origin側Release Pleaseを無効化するか、upstream同期pushを除外する条件
- merge commit方式で履歴を統合するPR
- originとupstreamのどちらをタグ・Releaseの唯一の権威にするかの明文化

この設計が完了するまで、`git push --force origin upstream/main:main`や`git reset --hard upstream/main`による同期は行いません。

## 13. Hotfix

hotfixは正式リリース元であるupstreamの最新`main`から作成します。

### 13.1 hotfix作成【ローカル・手動】

```bash
git fetch upstream --prune
git switch -c hotfix/<topic> upstream/main
```

### 13.2 upstreamへpush・PR作成【upstream側・手動】

```bash
git push -u upstream hotfix/<topic>
gh pr create \
  --repo ichipiro/IE_Event_Bot \
  --base main \
  --head hotfix/<topic> \
  --title "fix: describe the hotfix"
```

`hotfix/* → upstream:main`をmergeした後は、Release Please、タグ・GitHub Release、`upstream:main → upstream:develop`、`upstream:develop → origin:develop`の順で通常フローへ戻ります。

## 14. 手動工程と自動工程

| 順番 | 工程 | 操作場所 | 区分 |
| ---: | --- | --- | --- |
| 1 | featureブランチをcommit | ローカル | 手動 |
| 2 | featureブランチをpush | origin側 | 手動 |
| 3 | upstream/develop向けPR作成 | origin → upstream | 手動 |
| 4 | feature PR checks | upstream側 | 自動 |
| 5 | feature PRレビュー・merge | upstream側 | 手動 |
| 6 | releaseブランチ作成 | ローカル | 手動 |
| 7 | releaseブランチをpush | upstream側 | 手動 |
| 8 | upstream/main向けPR作成 | upstream側 | 手動 |
| 9 | release PR checks | upstream側 | 自動 |
| 10 | release PRレビュー・merge | upstream側 | 手動 |
| 11 | Release Please PR作成・更新 | upstream側 | 自動 |
| 12 | Release Please PRレビュー・merge | upstream側 | 手動 |
| 13 | タグ・GitHub Release作成 | upstream側 | 自動 |
| 14 | main→develop同期ブランチ・PR作成 | upstream側 | 自動 |
| 15 | upstream同期PR checks | upstream側 | 自動 |
| 16 | upstream同期PRレビュー・merge | upstream側 | 手動 |
| 17 | origin・upstreamをfetch | ローカル | 手動、読み取りのみ |
| 18 | upstream/developを同期ブランチへmerge | ローカル | 手動 |
| 19 | 同期ブランチをpush | origin側 | 手動 |
| 20 | origin/develop向けPR作成 | origin側 | 手動 |
| 21 | origin同期PR checks | origin側 | 自動 |
| 22 | origin同期PRレビュー・merge | origin側 | 手動 |
| 23 | ancestryと差分を確認 | ローカル | 手動、読み取りのみ |

## 15. Merge方式

### 15.1 merge commitを推奨するPR

- `origin:feature/* → upstream:develop`
- `upstream:release/* → upstream:main`
- `upstream:sync/main-to-develop-* → upstream:develop`
- `origin:sync/upstream-develop-* → origin:develop`

特にsync PRをsquashすると、内容が同じでも元ブランチが祖先として記録されません。次回の`git rev-list`や`git merge-base`で同期済みと判定できず、重複mergeや競合の原因になります。

### 15.2 禁止事項

- 共有済みの`develop`、`main`をrebaseしない。
- `main`、`develop`へ直接pushしない。
- upstreamまたはoriginの共有ブランチをforce pushしない。
- 同期のために`reset --hard`を使用しない。
- sourceとtargetを確認せずに`git push upstream`を実行しない。

## 16. Branch protectionと必須checks

originとupstreamの`main`、`develop`には、次の設定を適用します。

- Pull Request経由の変更を必須にする。
- 必要なレビュー数を設定する。
- `CI / test`を必須にする。
- `Commitlint / lint`を必須にする。
- `PR Target Guard / guard`を必須にする。
- 管理者を含め、共有ブランチへの直接pushを禁止する。

文書上の規則だけではGitHubは操作を拒否しません。rulesetまたはbranch protectionが有効であることをGitHub設定で確認します。

## 17. Merge後の確認

### 17.1 upstream側

```bash
git fetch upstream --prune --tags
git log --oneline --decorate -10 upstream/main upstream/develop
gh release list --repo ichipiro/IE_Event_Bot
```

確認対象:

- release PRが`upstream:main`へmerge済み
- Release Please PRがmerge済み
- `vX.Y.Z`タグとGitHub Releaseが存在する
- `main → develop`同期PRがmerge済み

### 17.2 origin側

```bash
git fetch --prune --multiple origin upstream
git log --oneline --decorate -10 origin/develop upstream/develop
git merge-base --is-ancestor upstream/develop origin/develop
git status --short --branch
```

確認対象:

- upstream側の未同期コミットが0件
- 不要なsyncブランチが残っていない
- 作業ツリーがclean
- ローカル`develop`が`origin/develop`を追跡している

## 18. 現在の実装状態と既知の問題

以下は2026-08-29時点の確認結果です。

### 18.1 origin側

- Actionsで自動生成された`push`・`pull_request` runは0件。
- `workflow_dispatch`による手動runは成功する。
- Actions permissionsは有効で、workflowはactive。
- 実変更を含むPRでもGitHub Actions check suiteが作成されない。
- branch rulesetは存在するが`disabled`。
- `main`のbranch protectionは未設定。
- フォルダ平坦化に伴う`AGENTS.md`変更と、この運用文書・READMEの変更がローカルで未コミット。

したがって、origin同期PRの自動checksは現在期待どおりには生成されません。Actionsが復旧するまではローカル検証結果とGitHub差分を人が確認する必要があります。

### 18.2 upstream側

- 過去のcross-repository PRではCI、Commitlint、PR Target Guardが成功している。
- 現在のupstream CIは旧版で、Pyright検査をまだ含まない。
- 現在のupstream mainには旧版Release Please workflowが残っている。
- upstreamのActions Repository Secretsは0件で、`RELEASE_AUTOMATION_TOKEN`は未登録。
- GitHub Releaseと`v*`タグは0件。
- 過去の`main → develop`同期は自動タグ連鎖ではなく手動PRで実施されている。
- branch protectionと有効なrulesetは未設定。

originの`feature/v3`には修正版workflowが含まれていますが、upstreamで正しく自動化するには次が必要です。

1. `feature/v3`を`upstream/develop`へmergeする。
2. 修正版を`upstream:release/* → upstream:main`で昇格する。
3. upstreamに`RELEASE_AUTOMATION_TOKEN`を登録する。
4. branch protectionまたはrulesetを有効化する。
5. Release Please、タグ、同期PRの実動を確認する。

### 18.3 現在のbranch差分

- `origin/feature/v3`は`upstream/develop`より4コミット先行、0コミット遅れ。
- 比較上のmerge競合はない。
- upstream向け`feature/v3` PRは未作成。
- 現時点の`upstream/develop`はすでに`origin/develop`の祖先であるため、逆同期を先に実行しても`Already up to date.`になる。

## 19. トラブルシュート

### `fatal: couldn't find remote ref upstream`

誤った複数remote fetch構文を使用しています。

```bash
git fetch --prune --multiple origin upstream
```

### `fatal: Not possible to fast-forward, aborting.`

ローカルブランチにremote未反映コミットがあります。

```bash
git log --oneline --left-right origin/develop...develop
```

差分の所有者と目的を確認してから処理します。resetで消しません。

### 同期PRに同じコミットが再表示される

過去のsync PRをsquash mergeした可能性があります。

```bash
git merge-base upstream/develop origin/develop
git log --oneline --left-right upstream/develop...origin/develop
```

内容だけでなく祖先関係を確認し、以後のsync PRはmerge commit方式にします。

### PRを作成してもchecksが表示されない

PRのbaseリポジトリを確認します。

- `origin:feature/* → upstream:develop`: upstream側Actionsを確認する。
- `origin:sync/* → origin:develop`: origin側Actionsを確認する。

run自体が存在しない場合、runnerやjob内の失敗ではなく、Actionsへのイベント配送前に停止しています。

### Release Pleaseが失敗する

確認項目:

- workflowが`upstream:main`に存在する。
- `RELEASE_AUTOMATION_TOKEN`がupstream Repository Secretに登録されている。
- ContentsとPull requestsがread and writeになっている。
- tokenの有効期限が切れていない。
- `main`へのpush eventでrunが生成されている。

### タグはあるが同期PRが作られない

確認項目:

- タグ名が`v*`に一致する。
- `Sync main -> develop PR (auto)` runが存在する。
- `RELEASE_AUTOMATION_TOKEN`でbranch pushとPR作成ができる。
- 同名のopen PRがすでに存在しない。
