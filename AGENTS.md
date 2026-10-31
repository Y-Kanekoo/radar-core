# radar-core 開発ルール

## 範囲・言語

- 実装担当として、指定範囲の仕様を実装する。設計・最終レビューはユーザーまたは監督側が行う。
- Python 3.11以上、srcレイアウト、hatchling、import名radar_coreを維持する。
- 回答・ドキュメント・コメント・ログ・エラーメッセージは日本語。識別子は英語。
- 実行時依存は最小限とし、DB・MCP・LLM API・タグ付け・スコアリングを持ち込まない。
- 公開APIはsrc/radar_core/__init__.pyの__all__に明示する。py.typedを配布する。
- 仕様の曖昧さや設計変更は選択肢と推奨を示して指示を仰ぐ。無断の大規模変更をしない。
- 関連改善は実装せずTODO(autonomous):で記録する。
- ドキュメントとコードが食い違う場合は既存コードに従い、食い違いを報告する。

## 安全性・互換性

- 作業ディレクトリの外に書き込まない。他リポジトリはorigin/mainをgit showで読み取る。
- .envを作成・コミットしない。秘密情報や実在のWebhook URLをハードコードしない。
- 有料APIキーを要求する処理を導入しない。
- 既存テストを改変・削除・skipしない。実装側を修正し、新規テストで補う。
- テストでは外部ネットワークと実時間の待機を使わず、HTTPとsleepを注入する。
- Webhook URL・レスポンス本文・生の例外をログに出さない。
- 注入されたHTTPクライアントを閉じない。dry-runを送信成功として返さない。
- 許可なくcommit・push・ブランチ操作をしない。コミットは監督側が行う。
- コミットが明示許可された場合のみ、[type] 日本語の要約を使う。1目的1コミット、
  破壊的変更はBREAKING:を明記する。

## 検証

依存変更時はuv.lockを更新し、生成物として変更に含める。

```sh
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --locked --dev
./scripts/check.sh
```

check.shは以下を実行する。

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy --strict src
uv run --locked pytest
```

pytestのカバレッジは分岐込み85%以上を必須とし、ソケット通信を禁止する。
GitHub Actionsと.github/workflowsは追加しない。ローカル検証で全件通過後に完了を報告する。
人間が必要な作業はHUMAN_TODO.mdに「- [ ] 内容 #p1〜#p3 #タグ」の形式で記録する。
