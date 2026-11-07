"""
test.py
=======
フォロー対象ユーザーと検索キーワードから、実行予定の検索クエリだけを生成して確認する。

このスクリプトは Twitter API へリクエストを送らず、リツイートも行わない。
クエリ生成は本番（retweeting_bot.create_search_queries）と同じ関数を使う。

検索と判定まで実際に試したい場合は、`python retweeting_bot.py --dry-run` を使う
（検索の読み取り費用はかかるが、リツイートもファイル保存もしない）。
"""

from __future__ import annotations

from config import KEYWORDS
from retweeting_bot import create_search_queries
from tools.core import log_message, RETWEET_LOG_FILE


def run_query_preview() -> None:
    """
    config.KEYWORDS を使って検索クエリだけを生成し、内容を確認用に出力する。

    Twitter API の検索やリツイートは一切実行しない。
    """
    log_message(RETWEET_LOG_FILE, "=== クエリプレビュー開始 [DEFAULT] ===")

    queries = create_search_queries(KEYWORDS)
    for index, (users, query) in enumerate(queries, start=1):
        print(f"[QUERY {index}] ({len(users)}人, {len(query)}文字) {query}")

    log_message(RETWEET_LOG_FILE, f"=== クエリプレビュー完了 [DEFAULT]（{len(queries)}件） ===")


if __name__ == "__main__":
    run_query_preview()
