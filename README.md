# radar-core

RSS収集・正規化・Discord通知の共有ライブラリです。ai-radar Phase 6「core抽出」として、
ai-radar / qa-radar / connpass-notifier / claude-code-updates から段階的に利用できます。

Python 3.11以上、srcレイアウト、hatchling、型情報（py.typed）に対応しています。
実行時依存は httpx と feedparser のみです。DB・MCP・LLM API・タグ付け・
スコアリング・通知履歴の永続化は含みません。

## インストール

v0.1.0タグの公開後に、次のコマンドで追加します。

```sh
uv add "radar-core @ git+https://github.com/Y-Kanekoo/radar-core@v0.1.0"
```

## 正規化

```python
from radar_core.normalize import (
    compute_body_hash,
    make_snippet,
    normalize_published,
    normalize_url,
    strip_html,
)

url = normalize_url(
    "https://example.com/article/?utm_source=rss&campaign=weekly#top",
    extra_tracking_params={"campaign"},
)
assert url == "https://example.com/article"
assert strip_html("<p>こんにちは &amp; 世界</p>") == "こんにちは & 世界"
snippet = make_snippet("<p>記事本文</p>")  # 既定の本文上限は100字
body_hash = compute_body_hash("<p>記事本文</p>")
published_at = normalize_published((2024, 1, 15, 12, 30, 45))
```

ai-radarの期待値を維持し、scheme/hostの小文字化、追跡パラメータ・fragmentの除去、
arXivの数値形式のabs URLのversion除去、ルート以外の末尾スラッシュ除去を行います。
schemeなしのURLはそのまま返します。クエリの空値は元実装と同じく除去されます。
追加除去パラメータは大文字・小文字を区別しません。既定では意味を持ち得る
`src` / `source` は残します。

HTML除去と連続空白の圧縮後に抜粋し、英語は60%以降の単語境界を優先します。
**互換性の注意:** 元実装の「100字以内」という説明と異なり、元のテストは
「本文100字＋省略記号」で最大101字を期待します。この挙動を維持しています。
省略記号込み100字が必要な利用側は `make_snippet(body, max_chars=99)` を使います。
`max_chars` は1以上が必要です。これは技術上の互換仕様であり、公開可否の判断は利用側で行います。

本文ハッシュはHTML除去・空白圧縮後のUTF-8のSHA256です。
日時はUTCのタプルからUNIX秒へ変換し、不足する時刻要素は0で補います。
`None` は現在時刻になります。

## フィード取得・解析

```python
import asyncio
import httpx

from radar_core.fetch import RobotsCache, fetch_feed
from radar_core.parse import parse_feed


async def main() -> None:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        url = "https://example.com/feed.xml"
        if not await RobotsCache().is_allowed(url, client):
            return
        result = await fetch_feed(
            url,
            client=client,
            etag=None,  # 前回保存したETagを渡せる
            last_modified=None,  # 前回保存したLast-Modifiedを渡せる
            timeout=30.0,
            max_retries=2,
            user_agent="my-radar/1.0",
        )
        if result.is_not_modified:
            return
        if result.error:
            print(result.error)
            return
        if result.content is not None:
            feed = parse_feed(result.content)
            for item in feed.items:
                print(item.title, item.url)


asyncio.run(main())
```

`FetchResult` はstatus_code・content・etag・last_modified・errorを返します。
304では本文を返さず、渡されたキャッシュメタデータを維持します。
既定User-Agentは `radar-core/0.1.0` とリポジトリURL、タイムアウトは30秒です。
ai-radarとの互換のため、取得の再試行は既定0回です。
`max_retries` を指定すると、一時的なTransportErrorと5xxに指数バックオフを適用します。
UnsupportedProtocol・4xx・転送上限超過は再試行しません。
`retry_base_delay` は既定1秒です。負の再試行回数・待機時間は0として扱います。

`client` または `transport=httpx.MockTransport(handler)` を注入できます。同時指定はできません。
`sleep` に非同期関数を渡せば待機を差し替えられます。
内部生成したクライアントは終了時に閉じ、注入されたものは閉じません。
リダイレクトは内部生成時に有効です。注入時のリダイレクト設定は呼び出し元に従います。
`RobotsCache` は明示的に呼び出す独立した部品で、取得失敗・不在の場合は許可扱いです。
キャッシュはホスト単位なので、同じセッション中は同一ホストのschemeを統一してください。

`parse_feed` はRSS 2.0・Atom・RSS 1.0/RDFの本文（bytesまたはstr）から
`ParsedFeed(items, bozo, bozo_exception)` を返します。
文字列も本文として扱い、URL・ファイルとして開きません。
`ParsedItem` はguid・url・title・body・author・published_structを持ちます。
本文はcontent優先、なければsummary/description、日時はpublished優先、なければupdatedです。
タイトルがなければ元実装互換の `(no title)` を返します。
記事リンクのない音声フィードはaudio enclosureのURLを使います。音声は取得しません。
壊れたXMLでも抽出できた記事を返すため、bozoとitemsの両方を確認してください。

## Discord

```python
import os

from radar_core.discord import DiscordClient, build_embed, build_payloads

embed = build_embed(
    title="新しい記事",
    description="短い抜粋",
    url="https://example.com/article",
    footer="出所 / 著者",
    color=0x0969DA,
    fields=[{"name": "詳細", "value": "追加情報", "inline": False}],
)
payloads = build_payloads(content="更新があります", embeds=[embed])
with DiscordClient(os.environ["DISCORD_WEBHOOK_URL"]) as client:
    preview = client.send("更新があります", [embed], dry_run=True)
    assert preview.dry_run
    result = client.send("更新があります", [embed])
    for message in result.messages:
        if message.success:
            print(message.message_id, message.channel_id)
        else:
            print(message.error)
```

非同期の利用側は `AsyncDiscordClient` を使います。

```python
from radar_core.discord import AsyncDiscordClient


async def notify(webhook_url: str) -> list[str]:
    async with AsyncDiscordClient(webhook_url) as client:
        result = await client.send(content="更新があります")
        return result.message_ids
```

- contentは2000文字、embedは10個かつ合計6000文字ごとにメッセージを分割します。
  contentは欠落なく、可能なら改行位置で分割します。
- embedはtitle 256、description 4096、author.name 256、footer.text 2048、
  fieldsは25個・name 256・value 1024に切り詰めます。1 embed自体も合計6000文字以内です。
  絵文字を分断せずUTF-16単位で保守的に制限します。入力の辞書は変更しません。
  収まらないフィールドは除外します。重要情報はtitle・descriptionを優先してください。
- `clip(text, limit)` は省略記号込みで切り詰めます。`normalize_embed` は既存辞書にも使えます。
- 分割したcontentとembed群を順に対応付け、余った群は独立したメッセージにします。
  `allowed_mentions={"parse": []}` で通知メンションを無効にします。
- 既定は `wait=True`。URLの既存クエリを残してwaitだけ上書きし、
  message_idが確認できた場合だけ成功とします。`wait=False` は2xxを成功とし、IDを返しません。
- `SendResult.messages` は各メッセージのsuccess・message_id・channel_id・status_code・errorです。
  一部が失敗しても後続は送信し、`success` は全件成功時だけtrueになります。
  `message_ids` は確認済みID一覧です。空入力は無送信・success=falseです。
- `dry_run=True` は通信せず、送信予定の `payloads` を返します。
  dry-runを送信済みとして記録しないため、success=false・messages=[]になります。
- 429だけ、JSONのretry_after、次にRetry-Afterヘッダ、どちらも不正なら1秒待機します。
  有限の非負値を使い、既定2回再試行します。`max_retries` で変更できます。
  ネットワークエラー・5xxは重複送信を避けるため自動再送しません。
- 同期は `client=httpx.Client(...)`・`sleep=同期関数`、非同期は
  `client=httpx.AsyncClient(...)`・`sleep=非同期関数` を注入できます。
  送信タイムアウトは既定20秒。内部クライアントは送信終了時に閉じ、外部クライアントは閉じません。
- ログ・reprではWebhook URLをマスクします。httpxとhttpcoreのリクエスト行にも
  トークンを除くログフィルタを適用します。例外本文・レスポンス本文はログに出しません。
  独自のHTTPフックでURLを記録する場合は `mask_webhook_url` を使ってください。

上限とwaitの仕様:
[Discord Message](https://docs.discord.com/developers/resources/message#embed-object) /
[Discord Webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook)

## 抽出範囲と段階的移行

ai-radarの `crawler/{fetch,parse,normalize}.py` の取得結果・解析データ・正規化挙動を抽出しました。
`tests/test_*_compat.py` はorigin/mainの元テストからimport先のみを差し替えて移植
（整形を含む）し、期待値を維持しています。
元の追跡パラメータのコメントは19個ですが、集合の実体は20個で、実体を維持しました。
取得の任意リトライはqa-radarを参考にしています。

Discordはai-radarの記事embed生成・429処理を汎用化し、connpass-notifierの10 embed分割、
qa-radarのcontent送信、claude-code-updatesのUTF-16切り詰め・wait確認を取り入れました。
ai-radar固有のFeedItem・hype判定・カテゴリ別環境変数の解決・DB通知履歴は含めません。

1. 各利用側で依存を追加し、既存の薄いラッパー内から正規化・取得・解析を順次呼び出します。
   利用側の既存テストと実データの差分を先に確認します。
2. ai-radar / qa-radarのDiscord記事モデルは利用側に残し、
   snippet・tags・source/author・timestampを汎用embedへ変換します。
   既存文面を保つ場合はdescriptionを従来の2048文字で事前に切り、
   hypeの接頭辞なども利用側で付与します。
3. connpass-notifierは同期DiscordClientに既存のcontent/embedsを渡し、
   qa-radarは同期・非同期の呼び出し元に合わせてクライアントを選びます。
4. claude-code-updatesは既存の通知状態・公開確認を維持し、送信部分だけ置き換えます。
   現行payloadのcontent/embedsを渡し、確認済みmessage_idを永続化します。
   再試行方針の違い（本ライブラリは429のみ）も移行側で確認します。

qa-radarの旧URL正規化には末尾スラッシュ・arXiv version除去がなく、
移行すると重複判定結果が変わる可能性があります。保存済みURLの差分確認・移行は利用側で行います。
送信とDB更新はトランザクションではありません。分割メッセージの部分成功を記録し、
タイムアウト後の再送で重複し得ることを利用側で扱います。
このリポジトリの初期実装では、利用側リポジトリの変更は行っていません。

## ローカル検証

```sh
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --locked --dev
./scripts/check.sh

# 個別実行
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy --strict src
uv run --locked pytest
uv build
```

pytestはソケット通信を禁止し、MockTransportとインラインのフィードだけで動作します。
カバレッジは分岐込み85%以上を必須にしています。開発依存もuv.lockで固定しています。
GitHub Actionsは無料枠の都合で使わず、ローカル検証のみとします。
workflowsは追加しません。タグ公開・利用側移行の確認事項はHUMAN_TODO.mdに記載しています。

## ライセンス

MIT。コードを抽出したai-radar / qa-radarもY-KanekooのMITライセンスです。[LICENSE](LICENSE)を参照してください。
