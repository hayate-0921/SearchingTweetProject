"""
test.py
=======
フォロー対象ユーザーと検索キーワードから、実行予定の検索クエリだけを生成して確認する。

このスクリプトは Twitter API へリクエストを送らず、リツイートも行わない。
"""

from __future__ import annotations

from typing import List, Sequence

from config import KEYWORDS, MAX_USERS_PER_SEARCH
from tools.core import log_message, RETWEET_LOG_FILE
from tools.data_manager import load_following_list
from tools.twitter_api import build_search_query


# ------------------------------------------------------------
# 検索クエリ生成
# ------------------------------------------------------------
def create_search_queries(keywords: Sequence[str]) -> List[str]:
    """
    following.json と指定された keywords を用いて、
    MAX_USERS_PER_SEARCH ごとに検索クエリを分割生成する。

    Args:
        keywords (Sequence[str]):
            検索に使用するキーワードのリスト。

    Returns:
        List[str]:
            Twitter API v2 search_recent_tweets 用の検索クエリ文字列のリスト。

    Raises:
        ValueError:
            following.json に username が存在しない場合。
            keywords が空の場合。
    """
    if not keywords:
        raise ValueError("検索キーワードが指定されていません。")

    usernames = load_following_list()
    if not usernames:
        raise ValueError("following.json に username が存在しません。")

    log_message(
        RETWEET_LOG_FILE,
        f"[DEBUG] following.json 読み込み: {len(usernames)}ユーザー",
    )
    log_message(
        RETWEET_LOG_FILE,
        f"[DEBUG] 使用キーワード: {list(keywords)}",
    )

    queries: List[str] = []

    for i in range(0, len(usernames), MAX_USERS_PER_SEARCH):
        chunk = usernames[i : i + MAX_USERS_PER_SEARCH]
        log_message(
            RETWEET_LOG_FILE,
            f"[DEBUG] クエリ対象ユーザー: {chunk}",
            print_to_console=False,
        )

        query = build_search_query(
            usernames=chunk,
            keywords=keywords,
            exclude_retweets=True,
            exclude_replies=True,
        )

        log_message(
            RETWEET_LOG_FILE,
            f"[DEBUG] 生成クエリ: {query}",
            print_to_console=False,
        )
        queries.append(query)

    return queries


# ------------------------------------------------------------
# クエリ確認専用タスク
# ------------------------------------------------------------
def run_query_preview() -> None:
    """
    config.KEYWORDS を使って検索クエリだけを生成し、内容を確認用に出力する。

    Twitter API の検索やリツイートは一切実行しない。
    """
    log_message(
        RETWEET_LOG_FILE,
        "=== クエリプレビュー開始 [DEFAULT] ===",
    )

    queries = create_search_queries(KEYWORDS)
    for index, query in enumerate(queries, start=1):
        print(f"[QUERY {index}] {query}")

    log_message(
        RETWEET_LOG_FILE,
        f"=== クエリプレビュー完了 [DEFAULT]（{len(queries)}件） ===",
    )


if __name__ == "__main__":
    run_query_preview()
