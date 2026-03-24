# Public Release Checklist

## 目的
この文書は、`SearchingTweetProject` を public にする前に確認すべき項目をまとめたものです。

## チェックリスト

- [ ] `git status --short` がクリーンである
- [ ] `git ls-files` に `.env` や `logs/*.log` が含まれていない
- [ ] `git log --oneline -- logs/retweeting.log` の結果を確認した
- [ ] 履歴に機密ログが残る場合、`git filter-repo` または新規 public repo 作成の方針を決めた
- [ ] GitHub Secrets を確認した
- [ ] `test.py` が dry-run であることを確認した
- [ ] `retweeting_bot.py` が本番実行用であることを理解している
- [ ] `data/following.json` と `data/retweeted.json` を公開して問題ないと判断した

## 履歴洗浄の基本手順

```bash
git clone --mirror https://github.com/hayate-0921/SearchingTweetProject.git repo-cleanup.git
cd repo-cleanup.git
git filter-repo --path logs/retweeting.log --invert-paths
git push --force --mirror origin
```

## 注意事項

- 履歴書き換えは共同開発者の clone に影響します
- 既に外部へ漏えいした Secrets は、履歴削除とは別にローテーションが必要です
- 現在の repo では `logs/*.log` は ignore 対象ですが、過去コミットまでは自動では消えません
