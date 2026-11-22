# SearchingTweetProject

Twitter/X API を利用して、指定ユーザーの投稿から条件に合うものを検索し、
自動でリツイートする Bot プロジェクトです。

扱うデータの設計は [docs/data_design.md](docs/data_design.md) にまとめています。

## 現在のファイル構成

```text
SearchingTweetProject/
├── .github/
│   └── workflows/
│       └── retweet.yml
├── .gitattributes
├── .gitignore
├── README.md
├── config.py
├── connection_check.py
├── analyze_observations.py
├── build_youtube_channels.py
├── compare_query_counts.py
├── init_database.py
├── inspect_query_hits.py
├── data/
│   ├── bot.db                  # 記録用データベース（SQLite。対象者・歌ってみた・告知・実行記録）
│   ├── following.json          # 検索対象のアカウント
│   ├── retweeted.json          # リツイート済みのツイート ID
│   ├── retweeted_videos.json   # リツイート済みの YouTube 動画 ID → ツイート ID
│   ├── pending.json            # 上限超過で翌日に回した候補（実行時に作成）
│   ├── state.json              # 前回どこまで検索したか（実行時に作成）
│   ├── youtube_channels.json   # X の username → YouTube チャンネル ID（build_youtube_channels.py で作成）
│   ├── youtube_watch.json      # 告知を探している歌ってみた動画（実行時に作成）
│   ├── observations/           # 実行ごとの観測記録
│   ├── query_counts/           # compare_query_counts.py の結果
│   └── query_inspections/      # inspect_query_hits.py の結果
├── docs/
│   └── data_design.md          # データ設計
├── requirements.txt
├── retweeting_bot.py
├── test.py
├── tests/
│   ├── test_database.py
│   ├── test_phase12.py
│   └── test_youtube_fallback.py
└── tools/
    ├── __init__.py
    ├── core.py
    ├── database.py
    ├── data_manager.py
    ├── observation.py
    ├── twitter_api.py
    ├── video_filter.py
    ├── youtube_fallback.py
    ├── youtube_feed.py
    └── youtube_url.py
```

## 実行スクリプト

### `retweeting_bot.py`
本番用スクリプトです。処理の流れは次のとおりです。

1. 前回の実行で翌日に回した候補（`pending.json`）を先に処理する
2. 前回検索した終わりの時刻〜本日 0:00（JST）を、全クエリ・全ページ検索する
   - YouTube リンクは `url:"youtube.com" OR url:"youtu.be"` で判定する
     （検索 API では単語 `youtube` は本文にしか当たらず、リンクだけの告知を取りこぼすため）
   - ショート（`youtube.com/shorts`）と配信（`youtube.com/live`）のリンクを含む投稿は、
     `-url:` で検索の段階から除外する（読み取り費用もかからない）
   - 実行が失敗した日があっても、次の実行で続きから検索する（最大 7 日）
3. 取得後に判定する（`tools/video_filter.py`）
   - ショート（`/shorts/` リンク）、配信（`/live/` リンクのみ）、除外ワードを含む投稿を除外
   - 同じ動画は 1 回だけリツイートする（過去にリツイート済みの動画、同じ実行内の 2 件目以降を除外）
   - 同じ動画の告知が複数あれば、いちばん早い投稿を採用する
4. YouTube を起点に、取りこぼした告知を探し直す（Phase 3。下記）
5. 投稿時刻の早い順にリツイートし、上限（`RETWEET_LIMIT`）を超えた分は `pending.json` に回す
6. リツイート済み ID・動画 ID・実行状態・監視中の動画・観測記録を保存する
7. 対象者・歌ってみた動画・リツイートした告知・実行の概要を `data/bot.db` に記録する
   （記録に失敗してもリツイートには影響しない）

#### Phase 3: YouTube を起点にした取りこぼし対策
通常の検索（キーワード + YouTube リンク）では、次の告知を取りこぼすことがあります。

- 本文に検索ワードがない（例:「24時からプレミア公開！」と曲名だけ）
- X 側で URL が検索の索引に入らず、`url:` 条件に当たらない

そこで、タレントの YouTube チャンネルの RSS（API キー不要・無料）から歌ってみたを見つけ、
通常の検索で告知が見つからなかった動画について、X で本人の投稿を探し直します
（`tools/youtube_feed.py`、`tools/youtube_fallback.py`）。

1. RSS の動画のうち、タイトルが歌ってみた（`YOUTUBE_COVER_TITLE_PATTERN`）のものを
   `youtube_watch.json` に加える（ショート・歌枠のアーカイブ・リツイート済みの動画は除く）
2. タレントごとに `from:本人 (url:"youtube.com" OR url:"youtu.be")` で探す（YouTube リンクを含む投稿だけ読む）
3. 見つからなければ `from:本人` を読む（公開の前後に絞り、上限 `YOUTUBE_FULL_SCAN_MAX_POSTS` 件）
4. 対象の動画 ID を含む投稿だけを、通常の判定に回す（無関係な投稿はリツイートしない）

動画ごとに「どこまで探したか」を残し、次の実行ではその続きだけを探します。
公開から `YOUTUBE_WATCH_DAYS` 日たった動画は探すのをやめます。
YouTube や X の一時的な失敗があっても、通常のリツイートは続けます。
X で告知されていない動画は、リツイートする元がないため拾えません。

```bash
python retweeting_bot.py            # 本番
python retweeting_bot.py --dry-run  # 検索と判定だけ（リツイート・保存なし。読み取り費用はかかる）
```

### `build_youtube_channels.py`（Phase 3）
にじさんじ公式サイトのタレント情報から、`data/youtube_channels.json`
（X の username → YouTube チャンネル ID）を作ります。X API は呼びません。
`following.json` に入っていないタレントがいれば一覧を表示するので、追加漏れの確認にも使えます。
新しいタレントがデビューしたときなど、`following.json` を更新したら実行し直してください。
あわせて、`data/bot.db` の対象者マスタに表示名を入れます。

```bash
python build_youtube_channels.py            # 対応表を作って保存
python build_youtube_channels.py --dry-run  # 保存せずに結果だけ表示
```

### `init_database.py`（データベースの作成）
`data/bot.db` を作り、これまでの観測記録と `retweeted_videos.json` を取り込みます（最初に 1 回だけ）。
X API・YouTube は呼びません。以後は `retweeting_bot.py` が実行のたびに記録します。
テーブルの内容と集計の例は [docs/data_design.md](docs/data_design.md) を参照してください。

```bash
python init_database.py          # data/bot.db がなければ作る
python init_database.py --force  # 作り直す
```

`bot.db` はバイナリのため、GitHub Actions の更新とぶつかると自動でマージできません。
ローカルでは本番の実行（dry-run 以外）をしないでください。

### `test.py`
クエリ確認用のスクリプトです。

- 検索クエリ文字列だけを生成します（本番と同じ関数を使用）
- Twitter API の検索は実行しません
- リツイートもしません

### `tests/`（自動テスト）
X API を呼ばずに、判定ロジックと Bot 全体の流れを確認します。
直近 7 日間の実データ 37 件（本文なし）を使い、新作 16 本だけが残ることを確かめます。

```bash
python -m unittest discover -s tests -v
```

### `analyze_observations.py`（Phase 0: 計測）
本番の実行ごとに `data/observations/YYYY-MM-DD.json` へ保存される観測記録を集計します。

- X API は呼び出しません（費用なし）
- 費用の概算、取りこぼしの兆候（打ち切られた検索・上限で見送った投稿）、
  取りすぎの兆候（ショート・同じ動画の複数告知など）を表示します
- 観測記録にはツイート本文を保存せず、ID と判定材料だけを保存します

```bash
python analyze_observations.py --days 7
```

### `compare_query_counts.py`（Phase 0: 計測）
検索条件の案ごとに、直近 7 日間のヒット件数だけを数えて比較します。

- X API の Counts: Recent を使います（件数に関係なく 1 リクエスト $0.005）
- `--yes` を付けない限り API は呼ばず、費用の見積もりだけを表示します
- 手動実行専用です（GitHub Actions からは実行しません）

```bash
python compare_query_counts.py          # 見積もりのみ
python compare_query_counts.py --yes    # 実行（213 ユーザー × 4 案で約 $0.44）
```

### `inspect_query_hits.py`（Phase 0: 計測）
検索条件に当たる直近 7 日間の投稿を実際に取得し、`retweeted.json` と照合して取りこぼしを調べます。

- 取得件数 × $0.005 の費用がかかります（`--yes` を付けない限り API は呼びません）
- 本文は画面に表示するだけで、`data/query_inspections/` には ID と判定材料だけを保存します

### `connection_check.py`
接続確認専用スクリプトです。

- `get_me()` のみを使用します
- 課金系の検索クエリは消費しません
- API 認証が通るかだけを確認します

## GitHub Actions

本プロジェクトは GitHub Actions により自動実行されます。

- 実行スクリプト: `retweeting_bot.py`
- 実行頻度: 毎日 JST 0:00（cron `0 15 * * *` は UTC 表記）
- 定義ファイル: `.github/workflows/retweet.yml`
- 永続化対象: `data/` 以下（JSON・`observations/`・`bot.db`）。実行後にリポジトリへコミットされる

## 必要な Secrets

以下を GitHub Secrets に登録してください。

- `API_KEY`
- `API_SECRET`
- `ACCESS_TOKEN`
- `ACCESS_SECRET`
- `BEARER_TOKEN`

## 公開前チェック

### 1. 現在のツリーにログや `.env` が含まれていないことを確認する
このリポジトリでは以下を git 管理対象外としています。

- `.env`
- `logs/*.log`

公開前に、これらが `git status` や `git ls-files` に出てこないことを確認してください。

### 2. 過去の Git 履歴に機密ログが残っていないか確認する
`logs/retweeting.log` を過去に commit 済みの場合、
現在のブランチから削除していても履歴上は参照できる可能性があります。

確認コマンド例:

```bash
git log --oneline -- logs/retweeting.log
git log --all --stat -- logs/retweeting.log
```

### 3. 履歴に残っている場合の修正方法
この修正は通常の PR だけでは完結しません。
**履歴書き換え + force push** が必要です。

代表的な方法は `git filter-repo` です。

```bash
# 1) 念のため mirror clone を作成
git clone --mirror <YOUR_REPO_URL> repo-cleanup.git
cd repo-cleanup.git

# 2) 問題ファイルを履歴から完全削除
git filter-repo --path logs/retweeting.log --invert-paths

# 3) 必要なら他の生成物や機密ファイルも同時に除去
# git filter-repo --path logs/retweeting.log --path .env --invert-paths

# 4) 内容確認
git log --oneline -- logs/retweeting.log

# 5) リモートへ反映
# 既存の公開履歴を置き換えるため force push が必要
git push --force --mirror origin
```

補足:

- 履歴を書き換えた後は、既存 clone 側でも再 clone か hard reset が必要です
- 以前ログに認証情報断片が出ていた場合は、念のため Secrets のローテーションも推奨します
- もし履歴を書き換えたくない場合は、**新しい public リポジトリを作り、現在の安全な状態だけを載せ直す** 方法もあります

### 4. 公開前に README と公開対象データを再確認する
この README は現在の tracked files に合わせて更新しています。
あわせて以下が公開されて問題ないか確認してください。

- `data/following.json`
- `data/retweeted.json`

これらは機密情報ではありませんが、運用方針や処理履歴は読み取れます。
