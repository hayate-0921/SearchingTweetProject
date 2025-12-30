"""
retweeting_bot.py
======================
フォロー対象ユーザー（username）とキーワードに基づいて
新規ツイートを検索し、自動でリツイートするバッチジョブ。
"""

from __future__ import annotations

import sys
import traceback

from typing import List

from config import (
    KEYWORDS,
    MAX_TWEETS_PER_SEARCH,
    RETWEET_LIMIT,
    MAX_USERS_PER_SEARCH,
)

from tools.core import log_message, get_previous_day_range_utc, RETWEET_LOG_FILE
from tools.data_manager import (
    load_following_list,
    load_retweeted_list,
    save_retweeted_list,
)

from tools.twitter_api import (
    create_client,
    build_search_query,
    search_tweets,
    retweet,
)




# ------------------------------------------------------------
# 検索クエリ生成
# ------------------------------------------------------------
def create_search_queries() -> List[str]:
    """
    following.json と config の KEYWORDS を使い、
    MAX_USERS_PER_SEARCH ごとに検索クエリを分割して生成する。
    """
    usernames = load_following_list()
    if not usernames:
        raise ValueError("following.json に username が存在しません。")

    log_message(RETWEET_LOG_FILE, f"[DEBUG] following.json 読み込み: {usernames}")

    queries = []
    for i in range(0, len(usernames), MAX_USERS_PER_SEARCH):
        chunk = usernames[i:i + MAX_USERS_PER_SEARCH]
        log_message(RETWEET_LOG_FILE, f"[DEBUG] クエリ対象ユーザー: {chunk}")

        query = build_search_query(
            user_identifiers=chunk,
            identifiers_type="username",
            keywords=KEYWORDS,
            exclude_retweets=True,
            exclude_replies=True,
        )
        log_message(RETWEET_LOG_FILE, f"[DEBUG] 生成クエリ: {query}")
        queries.append(query)

    return queries


# ------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------
def main() -> None:
    log_message(RETWEET_LOG_FILE, "=== リツイートジョブ開始 ===")

    try:
        # --- API クライアント作成 ---
        client = create_client()

        # --- 過去に RT したツイート IDs ---
        retweeted_ids = load_retweeted_list()
        total_retweets = 0

        # --- 日付範囲（API は UTC 指定） ---
        start_time, end_time = get_previous_day_range_utc()

        log_message(RETWEET_LOG_FILE, f"[DEBUG] start_time={start_time}")
        log_message(RETWEET_LOG_FILE, f"[DEBUG] end_time={end_time}")

        # --- クエリ生成 ---
        queries = create_search_queries()
        if not queries:
            log_message(RETWEET_LOG_FILE, "[ERROR] 検索クエリ生成に失敗")
            return

        # --- 各クエリ実行 ---
        for query in queries:
            log_message(RETWEET_LOG_FILE, f"[DEBUG] 検索クエリ実行: {query}")
            
            # ★ Raw response を取得できるよう return_raw=True
            raw_resp = search_tweets(
                client=client,
                query=query,
                start_time=start_time,
                end_time=end_time,
                max_results=MAX_TWEETS_PER_SEARCH,
                return_raw=True,
            )

            # --- Raw レスポンスの情報をデバッグ出力 ---
            try:
                meta = getattr(raw_resp, "meta", None)
                log_message(RETWEET_LOG_FILE, f"[DEBUG] Raw API meta: {meta}")
            except Exception:
                log_message(RETWEET_LOG_FILE, "[DEBUG] Raw response meta 参照不可")

            tweets = getattr(raw_resp, "data", None) or []
            log_message(RETWEET_LOG_FILE, f"[DEBUG] 取得ツイート件数: {len(tweets)}")

            if not tweets:
                continue

            # --- 各ツイート処理 ---
            for tw in tweets:
                tid = str(tw.id)

                if tid in retweeted_ids:
                    continue

                if total_retweets >= RETWEET_LIMIT:
                    log_message(RETWEET_LOG_FILE, "リツイート上限に達しました。")
                    save_retweeted_list(retweeted_ids)
                    log_message(RETWEET_LOG_FILE, "=== リツイート終了（上限到達） ===")
                    return

                try:
                    retweet(client, tw.id)
                    retweeted_ids.add(tid)
                    total_retweets += 1
                    preview = tw.text.replace("\n", " ")[:50]
                    log_message(RETWEET_LOG_FILE, f"リツイート成功: {tid} / {preview}...")
                except Exception as e:
                    log_message(RETWEET_LOG_FILE, f"[ERROR] リツイート失敗: {tid} / {e}")
                    log_message(RETWEET_LOG_FILE, traceback.format_exc())

        save_retweeted_list(retweeted_ids)
        log_message(RETWEET_LOG_FILE, f"=== リツイート完了（{total_retweets}件） ===")

    except Exception as e:
        log_message(RETWEET_LOG_FILE, f"[FATAL] 例外内容: {e}")
        log_message(RETWEET_LOG_FILE, "[FATAL] Traceback:\n" + traceback.format_exc())
        sys.exit(1)


# ------------------------------------------------------------
# エントリポイント
# ------------------------------------------------------------
if __name__ == "__main__":
    main()

