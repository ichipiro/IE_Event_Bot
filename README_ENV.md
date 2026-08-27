# README_ENV

このファイルは、このリポジトリ用の `.venv` に入れる Python 依存だけをまとめたものです。

## 必要な Python バージョン

- Python `>=3.10`

CI は Python `3.12` を使用します。ローカル環境は上記の範囲内で作成してください。

## venv に入れるもの

`pyproject.toml` 由来:

- `ruff==0.15.10`
- `pytest==9.0.3`
- `pyright==1.1.411`

`workers/requirements.txt` 由来:

- `rsa==4.9.1`
- `google-auth==2.38.0`

## セットアップ例

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install -r workers/requirements.txt
```

## 補足

- `activate` は使わず、`.venv/bin/python` / `.venv/bin/pip` を直接使う前提です。
- `.[dev]` で入るのは開発依存の `ruff`、`pytest`、`pyright` です。
- Worker 実行に使う追加依存は `workers/requirements.txt` の `rsa` と `google-auth` です。
