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

### `connection_check.py`
接続確認専用スクリプトです。

- `get_me()` のみを使用します
- 課金系の検索クエリは消費しません
- API 認証が通るかだけを確認します

## GitHub Actions

本プロジェクトは GitHub Actions により自動実行されます。

- 実行スクリプト: `retweeting_bot.py`
- 実行頻度: 毎日 JST 9:00
- 定義ファイル: `.github/workflows/retweet.yml`
- 永続化対象: `data/*.json` のみ

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
