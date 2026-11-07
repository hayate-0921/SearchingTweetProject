# SearchingTweetProject

Twitter/X API を利用して、指定ユーザーの投稿から条件に合うものを検索し、
自動でリツイートする Bot プロジェクトです。

## 現在のファイル構成

```text
SearchingTweetProject/
├── .github/
│   └── workflows/
│       └── retweet.yml
├── .gitignore
├── README.md
├── config.py
├── connection_check.py
├── analyze_observations.py
├── compare_query_counts.py
├── inspect_query_hits.py
├── data/
│   ├── following.json          # 検索対象のアカウント
│   ├── retweeted.json          # リツイート済みのツイート ID
│   ├── retweeted_videos.json   # リツイート済みの YouTube 動画 ID → ツイート ID
│   ├── pending.json            # 上限超過で翌日に回した候補（実行時に作成）
│   ├── state.json              # 前回どこまで検索したか（実行時に作成）
│   ├── observations/           # 実行ごとの観測記録
│   ├── query_counts/           # compare_query_counts.py の結果
│   └── query_inspections/      # inspect_query_hits.py の結果
├── requirements.txt
├── retweeting_bot.py
├── test.py
├── tests/
│   └── test_phase12.py
└── tools/
    ├── __init__.py
    ├── core.py
    ├── data_manager.py
    ├── observation.py
    ├── twitter_api.py
    ├── video_filter.py
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
4. 投稿時刻の早い順にリツイートし、上限（`RETWEET_LIMIT`）を超えた分は `pending.json` に回す
5. リツイート済み ID・動画 ID・実行状態・観測記録を保存する

```bash
python retweeting_bot.py            # 本番
python retweeting_bot.py --dry-run  # 検索と判定だけ（リツイート・保存なし。読み取り費用はかかる）
```

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
- 永続化対象: `data/` 以下（`following.json`、`retweeted.json`、`observations/`）

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
