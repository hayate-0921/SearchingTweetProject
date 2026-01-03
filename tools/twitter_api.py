"""
twitter_api.py
---------------
Twitter (X) API へのアクセス機能をまとめたモジュール。

本モジュールは「API 呼び出し」のみに責務を限定し、
設定値の読み込み・ローカルデータ管理・ビジネスロジックは扱わない。

提供する関数:
  - create_client()
  - build_search_query(...)
  - search_tweets(...)
  - retweet(...)
  - tweet_rate_limit_exceeded_notice(...)

設計方針:
  - 認証情報は config.load_twitter_auth に集約する
  - Tweepy Response は動的属性を持つため getattr を用いて安全に扱う
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Union, cast
from datetime import datetime
import logging

import tweepy

from config import load_twitter_auth, TwitterAuth

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 1) Tweepy クライアント生成
# --------------------------------------------------------------------------
def create_client(suffix: str | None = None) -> tweepy.Client:
    """
    config.load_twitter_auth を用いて Twitter API 認証情報を取得し、
    Tweepy.Client を生成して返す。

    認証情報の妥当性チェックは load_twitter_auth 側で完結しているため、
    本関数では Client 初期化のみを責務とする。

    Args:
        suffix (str | None):
            使用する Twitter アカウント識別子。
            None の場合はサフィックスなしの環境変数を使用する。

    Returns:
        tweepy.Client:
            認証済み Tweepy クライアント。

    Raises:
        ValueError:
            環境変数が未設定・不正な場合。
        tweepy.TweepyException:
            クライアント初期化時に Tweepy 側で例外が発生した場合。
    """
    auth: TwitterAuth = load_twitter_auth(suffix)

    try:
        return tweepy.Client(
            bearer_token=auth.bearer_token,
            consumer_key=auth.api_key,
            consumer_secret=auth.api_secret,
            access_token=auth.access_token,
            access_token_secret=auth.access_secret,
            wait_on_rate_limit=True,
        )
    except tweepy.TweepyException as e:
        logger.error("Tweepy.Client の初期化に失敗しました: %s", e)
        raise


# --------------------------------------------------------------------------
# 2) 検索クエリ生成
# --------------------------------------------------------------------------
def build_search_query(
    usernames: Sequence[str],
    keywords: Sequence[str],
    exclude_retweets: bool = True,
    exclude_replies: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """
    Twitter 検索 API（v2）用の検索クエリ文字列を組み立てる。
    
    Args:
        usernames (Sequence[str]):
            '@' を含まない Twitter username（screen_name）のリスト。
            'from:username' 形式に変換して OR 結合する。
        keywords (Sequence[str]):
            検索キーワードのリスト。
            各要素は OR 条件として結合される。
        exclude_retweets (bool):
            True の場合、リツイート（-is:retweet）を除外する。
        exclude_replies (bool):
            True の場合、リプライ（-is:reply）を除外する。
        start_date (str | None):
            検索開始日（YYYY-MM-DD）。
        end_date (str | None):
            検索終了日（YYYY-MM-DD）。

    Returns:
        str:
            Twitter API に渡す検索クエリ文字列。
            条件が一切指定されない場合は空文字列を返す。
    """
    parts: list[str] = []

    # --- ユーザー条件 ---
    if usernames:
        user_parts = [f"from:{u}" for u in usernames]
        parts.append("(" + " OR ".join(user_parts) + ")")

    # --- キーワード条件 ---
    if keywords:
        keyword_parts = [str(k) for k in keywords]
        parts.append("(" + " OR ".join(keyword_parts) + ")")

    # --- 除外条件 ---
    if exclude_retweets:
        parts.append("-is:retweet")
    if exclude_replies:
        parts.append("-is:reply")

    # --- 日付条件 ---
    if start_date:
        parts.append(f"since:{start_date}")
    if end_date:
        parts.append(f"until:{end_date}")

    return " ".join(parts)



# --------------------------------------------------------------------------
# 3) ツイート検索
# --------------------------------------------------------------------------
def search_tweets(
    client: tweepy.Client,
    query: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    max_results: int = 50,
    return_raw: bool = False,
) -> Union[List[tweepy.Tweet], tweepy.Response]:
    """
    Twitter API v2 の search_recent_tweets を実行する。

    Args:
        client (tweepy.Client):
            認証済みクライアント。
        query (str):
            検索クエリ文字列。
        start_time (datetime | None):
            検索開始時刻（UTC）。
        end_time (datetime | None):
            検索終了時刻（UTC）。
        max_results (int):
            取得件数（最大 100）。
        return_raw (bool):
            True の場合、Tweepy Response をそのまま返す。

    Returns:
        List[tweepy.Tweet] | tweepy.Response:
            return_raw=False の場合は Tweet のリスト。
            True の場合は API 生レスポンス。

    Raises:
        ValueError:
            query が空の場合。
        tweepy.TweepyException:
            API 呼び出しに失敗した場合。
    """
    if not query:
        raise ValueError("検索クエリが空です。")

    params = {
        "query": query,
        "max_results": max_results,
        "tweet_fields": ["id", "text", "author_id", "created_at"],
    }
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time

    try:
        resp = client.search_recent_tweets(**params)
    except tweepy.TweepyException as e:
        logger.error("search_recent_tweets に失敗しました: %s", e)
        raise

    if return_raw:
        return cast(tweepy.Response, resp)

    return getattr(resp, "data", None) or []


# --------------------------------------------------------------------------
# 4) リツイート
# --------------------------------------------------------------------------
def retweet(client: tweepy.Client, tweet_id: Union[str, int]) -> Optional[dict]:
    """
    指定されたツイート ID をリツイートする。

    Args:
        client (tweepy.Client):
            認証済みクライアント。
        tweet_id (str | int):
            リツイート対象のツイート ID。

    Returns:
        dict | None:
            API レスポンスの data 部分。
            失敗時は例外が送出される。

    Raises:
        tweepy.TweepyException:
            リツイートに失敗した場合。
    """
    try:
        resp = client.retweet(tweet_id)
        return getattr(resp, "data", None)
    except tweepy.TweepyException as e:
        logger.error("retweet に失敗しました (tweet_id=%s): %s", tweet_id, e)
        raise


# --------------------------------------------------------------------------
# 5) レート制限超過時の通知ツイート
# --------------------------------------------------------------------------
def tweet_rate_limit_exceeded_notice(
    client: tweepy.Client,
    message: str | None = None,
) -> Optional[dict]:
    """
    Twitter API の読み取り上限（Rate Limit）超過時に、
    その旨を通知するツイートを投稿する。

    Args:
        client (tweepy.Client):
            認証済みクライアント。
        message (str | None):
            投稿する文言。
            None の場合はデフォルト文言を使用する。

    Returns:
        dict | None:
            投稿結果の data 部分。

    Raises:
        tweepy.TweepyException:
            ツイート投稿に失敗した場合。
    """
    text = message or (
        "【自動通知】\n"
        "Twitter API の読み取り上限に達したため、"
        "一時的に検索処理を停止しています。"
    )

    try:
        resp = client.create_tweet(text=text)
        return getattr(resp, "data", None)
    except tweepy.TweepyException as e:
        logger.error("レート制限通知ツイートに失敗しました: %s", e)
        raise
