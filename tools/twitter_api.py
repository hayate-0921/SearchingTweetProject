"""
twitter_api.py
---------------
Twitter (X) API へのアクセス機能をまとめたモジュール。

このファイルは「API 呼び出し」に関する責務のみを持ち、
ローカル JSON 操作・ハッシュ管理・ユーザー設定管理などは一切含まない。
そのため、テスト・モック化・静的解析の切り離しが容易になる。

提供する関数:
  - create_client()
  - build_search_query(...)
  - search_tweets(...)
  - retweet(...)

注意:
  Tweepy の Response オブジェクトは実行時に data/meta が動的に付与されるため、
  静的解析ツール（Pylance 等）は属性アクセスを警告する場合がある。
  本モジュールでは getattr(..., "data", None) を用いて安全に扱う。
"""

from typing import List, Optional, Sequence, Union, cast
import logging
from datetime import datetime

import tweepy

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 1) Tweepy クライアント生成
# --------------------------------------------------------------------------
def create_client(
    bearer_token: str,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
) -> tweepy.Client:
    """
    Tweepy.Client を生成して返す。

    Args:
        bearer_token (str): Bearer Token
        api_key (str): API Key
        api_secret (str): API Secret
        access_token (str): Access Token
        access_secret (str): Access Token Secret

    Returns:
        tweepy.Client: 認証済みクライアント

    Raises:
        ValueError: 必須の認証情報が不足している場合
        tweepy.TweepyException: Tweepy 側の初期化エラー
    """

    if not all([bearer_token, api_key, api_secret, access_token, access_secret]):
        raise ValueError(
            "Twitter API の認証情報が不足しています。"
            "bearer_token / api_key / api_secret / access_token / access_secret"
        )

    try:
        client = tweepy.Client(
            bearer_token=bearer_token,
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
            wait_on_rate_limit=True,
        )
        return client

    except tweepy.TweepyException as e:
        logger.error("Tweepy.Client の初期化に失敗しました: %s", e)
        raise


# --------------------------------------------------------------------------
# 2) 検索クエリ生成
# --------------------------------------------------------------------------
def build_search_query(
    user_identifiers: Sequence[str],
    identifiers_type: str,
    keywords: Sequence[str],
    exclude_retweets: bool = True,
    exclude_replies: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
    ) -> str:
    """
    ユーザー識別子・キーワード・除外条件などから検索クエリ文字列を組み立てる。
    start_date / end_date を指定することで日付フィルタを追加できる。

    Args:
        user_identifiers (Sequence[str]):
            ユーザー名またはユーザーIDのリスト。
        identifiers_type (str):
            "username" または "id"。
        keywords (Sequence[str]):
            検索キーワード。
        exclude_retweets (bool):
            True の場合 "-is:retweet" を付加。
        exclude_replies (bool):
            True の場合 "-is:reply" を付加。
        start_date (str | None):
            取得開始日（例: "2025-01-01"）
        end_date (str | None):
            取得終了日（例: "2025-01-31"）

    Returns:
        str: 組み立てられた検索クエリ。

    Raises:
        ValueError: identifiers_type が不正な場合。
    """

    if identifiers_type not in ("username", "id"):
        raise ValueError("identifiers_type は 'username' か 'id' を指定してください。")

    # --- ユーザー部 ---
    user_query = ""
    if user_identifiers:
        user_parts = [f"from:{u}" for u in user_identifiers]
        user_query = "(" + " OR ".join(user_parts) + ")"

    # --- キーワード部 ---
    kw_query = ""
    if keywords:
        kw_parts = [str(k) for k in keywords]
        kw_query = "youtu (" + " OR ".join(kw_parts) + ")"

    # --- 除外条件 ---
    exclusions = []
    if exclude_retweets:
        exclusions.append("-is:retweet")
    if exclude_replies:
        exclusions.append("-is:reply")

    # --- 日付フィルタ部 ---
    date_filters = []
    if start_date:
        date_filters.append(f"since:{start_date}")
    if end_date:
        date_filters.append(f"until:{end_date}")

    # --- まとめる ---
    parts = [p for p in (user_query, kw_query) if p]
    parts.extend(exclusions)
    parts.extend(date_filters)

    return " ".join(parts) if parts else ""



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
    search_recent_tweets をラップし、Tweet オブジェクトのリスト
    または Raw API Response を返す。
    """
    if not query:
        raise ValueError("検索クエリが空です。")

    logger.debug("Executing search_tweets")
    logger.debug("Query: %s", query)
    if start_time:
        logger.debug("Start Time (UTC): %s", start_time.isoformat())
    if end_time:
        logger.debug("End Time (UTC):   %s", end_time.isoformat())

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
        logger.info(
            f"[REQUEST] Time={datetime.now().isoformat()} Query={query} "
            f"MaxResults={max_results}"
        )

        resp = client.search_recent_tweets(**params)
        
        # レスポンスヘッダをログ
        headers = getattr(resp, "headers", None)
        remaining = headers.get("x-rate-limit-remaining", "N/A") if headers else "N/A"

        logger.info(
            f"[RESPONSE] Status={getattr(resp, 'status_code', 'N/A')} "
            f"X-Rate-Limit-Remaining={remaining}"
        )
    except tweepy.TweepyException as e:
        logger.error("search_recent_tweets に失敗しました: %s", e)
        raise

    # return_raw が True なら生データを返す
    if return_raw:
        return cast(tweepy.Response, resp)

    return getattr(resp, "data", None) or []


# --------------------------------------------------------------------------
# 4) リツイート
# --------------------------------------------------------------------------
def retweet(client: tweepy.Client, tweet_id: Union[str, int]) -> Optional[dict]:
    """
    指定ツイート ID をリツイートする。

    Args:
        client (tweepy.Client): 認証済みクライアント
        tweet_id (str | int): リツイート対象 ID

    Returns:
        dict | None: レスポンス data

    Raises:
        tweepy.TweepyException: API 呼び出し失敗時
    """
    try:
        resp = client.retweet(tweet_id)
    except tweepy.TweepyException as e:
        logger.error("retweet に失敗しました (tweet_id=%s): %s", tweet_id, e)
        raise

    return getattr(resp, "data", None)

