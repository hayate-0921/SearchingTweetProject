# SearchingTweetProject

```
SearchingTweetProject/
├── .gitignore
├── README.md
├── config.py
├── data/
│   ├── following.json
│   └── retweeted.json
├── logs/
│   └── retweeting.log
├── requirements.txt
├── retweeting_bot.py
└── tools/
    ├── __init__.py
    ├── __pycache__/
    │   ├── __init__.cpython-310.pyc
    │   ├── core.cpython-310.pyc
    │   ├── data_manager.cpython-310.pyc
    │   └── twitter_api.cpython-310.pyc
    ├── core.py
    ├── data_manager.py
    └── twitter_api.py
```

## GitHub Actions

本プロジェクトは GitHub Actions により自動実行されます。

- 実行スクリプト: `retweeting_bot.py`
- 実行頻度: 毎日 JST 9:00
- 定義ファイル: `.github/workflows/retweet.yml`

### 必要な Secrets

以下を GitHub Secrets に登録してください。

- API_KEY
- API_SECRET
- ACCESS_TOKEN
- ACCESS_SECRET
- BEARER_TOKEN
