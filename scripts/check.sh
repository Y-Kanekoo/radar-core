#!/bin/sh
# リポジトリ直下で、ロック済み依存による検証を実行する。
set -eu
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="$PWD/.cache/uv"
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy --strict src
uv run --locked pytest
