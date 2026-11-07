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
├── data/
│   ├── following.json
│   └── retweeted.json
├── requirements.txt
├── retweeting_bot.py
├── test.py
└── tools/
    ├── __init__.py
    ├── core.py
    ├── data_manager.py
    └── twitter_api.py
```

## 実行スクリプト

### `retweeting_bot.py`
本番用スクリプトです。

- Twitter API の検索を実行します
- 条件に一致したツイートをリツイートします
- `data/retweeted.json` を更新します

### `test.py`
クエリ確認用の dry-run スクリプトです。

- 検索クエリ文字列だけを生成します
- Twitter API の検索は実行しません
- リツイートもしません

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
