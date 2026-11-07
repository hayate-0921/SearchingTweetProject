"""
retweeting_bot.py
======================
フォロー対象ユーザー（username）とキーワードに基づいて
新規ツイートを検索し、自動でリツイートするバッチジョブ。

本モジュールは「1 Twitter アカウント分のリツイート処理」を
1 タスクとして完結させる設計を採用している。
"""

from __future__ import annotations

import sys
import traceback
from typing import List, Sequence, Tuple

from config import (
    KEYWORDS,
    MAX_TWEETS_PER_SEARCH,
    RETWEET_LIMIT,
    MAX_USERS_PER_SEARCH,
)

from tools.core import (
    log_message,
    get_previous_day_range_utc,
    RETWEET_LOG_FILE,
)

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

from tools.observation import RunRecorder


# ------------------------------------------------------------
# 検索クエリ生成
# ------------------------------------------------------------
def create_search_queries(keywords: Sequence[str]) -> List[Tuple[List[str], str]]:
    """
    following.json と指定された keywords を用いて、
    MAX_USERS_PER_SEARCH ごとに検索クエリを分割生成する。

    Args:
        keywords (Sequence[str]):
            検索に使用するキーワードのリスト。
            アカウント種別（COVER / ORIGINAL / STREAM 等）ごとに
            呼び出し側で切り替えることを想定する。

    Returns:
        List[Tuple[List[str], str]]:
            (対象ユーザーのリスト, search_recent_tweets 用の検索クエリ文字列) の組のリスト。
            対象ユーザーは観測記録（Phase 0）で使用する。

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

    queries: List[Tuple[List[str], str]] = []

    for i in range(0, len(usernames), MAX_USERS_PER_SEARCH):
        chunk = usernames[i : i + MAX_USERS_PER_SEARCH]
        log_message(
            RETWEET_LOG_FILE,
            f"[DEBUG] クエリ対象ユーザー: {chunk}",
            print_to_console=False
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
            print_to_console=False
        )
        queries.append((chunk, query))

    return queries


# ------------------------------------------------------------
# 1アカウント分のリツイートタスク
# ------------------------------------------------------------
def run_retweet_task(suffix: str | None = None) -> None:
    """
    指定された Twitter アカウント（suffix）に対応する検索キーワードを
    config から自動選択し、前日分ツイートの検索および
    自動リツイート処理を実行する。

    Args:
        suffix (str | None):
            使用する Twitter アカウントの識別子。
            None の場合はサフィックスなし（KEYWORDS）を使用する。

    Returns:
        None

    Raises:
        SystemExit:
            致命的な例外発生時に異常終了する。
    """
    # --------------------------------------------------------
    # アカウント識別ラベル
    # --------------------------------------------------------
    account_label = suffix or "DEFAULT"

    log_message(
        RETWEET_LOG_FILE,
        f"=== リツイートジョブ開始 [{account_label}] ===",
    )

    recorder: RunRecorder | None = None
    run_status = "error"

    try:
        # ----------------------------------------------------
        # 使用キーワードの決定
        # ----------------------------------------------------
        if suffix is None:
            keywords = KEYWORDS
            keywords_name = "KEYWORDS"
        else:
            keywords_attr = f"KEYWORDS_{suffix}"
            try:
                keywords = getattr(sys.modules["config"], keywords_attr)
                keywords_name = keywords_attr
            except AttributeError:
                raise ValueError(
                    f"config.py に {keywords_attr} が定義されていません。"
                )

        log_message(
            RETWEET_LOG_FILE,
            f"[DEBUG] 使用キーワード定義: {keywords_name} = {list(keywords)}",
        )

        # ----------------------------------------------------
        # API クライアント作成
        # ----------------------------------------------------
        client = create_client(suffix)

        # ----------------------------------------------------
        # リツイート済み ID 管理
        # ----------------------------------------------------
        retweeted_ids = load_retweeted_list()
        total_retweets = 0

        # ----------------------------------------------------
        # 日付範囲（UTC）
        # ----------------------------------------------------
        start_time, end_time = get_previous_day_range_utc()
        log_message(RETWEET_LOG_FILE, f"[DEBUG] start_time={start_time}")
        log_message(RETWEET_LOG_FILE, f"[DEBUG] end_time={end_time}")

        # ----------------------------------------------------
        # 観測記録（Phase 0）: API を追加で呼ばないため費用は増えない
        # ----------------------------------------------------
        recorder = RunRecorder(account_label, start_time, end_time, keywords)

        # ----------------------------------------------------
        # 検索クエリ生成
        # ----------------------------------------------------
        queries = create_search_queries(keywords)
        if not queries:
            log_message(
                RETWEET_LOG_FILE,
                "[ERROR] 検索クエリ生成に失敗",
            )
            run_status = "no_queries"
            return
        recorder.set_total_queries(len(queries))

        # ----------------------------------------------------
        # 各クエリ実行
        # ----------------------------------------------------
        for chunk, query in queries:
            log_message(
                RETWEET_LOG_FILE,
                f"[DEBUG] 検索クエリ実行: {query}",
            )

            raw_resp = search_tweets(
                client=client,
                query=query,
                start_time=start_time,
                end_time=end_time,
                max_results=MAX_TWEETS_PER_SEARCH,
                return_raw=True,
            )

            meta = getattr(raw_resp, "meta", None)
            log_message(
                RETWEET_LOG_FILE,
                f"[DEBUG] Raw API meta: {meta}",
            )

            tweets = getattr(raw_resp, "data", None) or []
            log_message(
                RETWEET_LOG_FILE,
                f"[DEBUG] 取得ツイート件数: {len(tweets)}",
            )
            recorder.add_search(query, chunk, meta, tweets)

            if not tweets:
                continue

            for tw in tweets:
                tid = str(tw.id)

                if tid in retweeted_ids:
                    recorder.set_decision(tid, "already_retweeted")
                    continue

                if total_retweets >= RETWEET_LIMIT:
                    recorder.set_decision(tid, "limit_reached")
                    log_message(
                        RETWEET_LOG_FILE,
                        "リツイート上限に達しました。",
                    )
                    save_retweeted_list(retweeted_ids)
                    log_message(
                        RETWEET_LOG_FILE,
                        f"=== リツイート終了 [{account_label}]（上限到達） ===",
                    )
                    run_status = "limit_reached"
                    return

                try:
                    retweet(client, tw.id)
                    retweeted_ids.add(tid)
                    total_retweets += 1
                    recorder.set_decision(tid, "retweeted")

                    preview = tw.text.replace("\n", " ")[:50]
                    log_message(
                        RETWEET_LOG_FILE,
                        f"リツイート成功: {tid} / {preview}...",
                    )
                except Exception as e:
                    recorder.set_decision(tid, "failed", detail=str(e)[:200])
                    log_message(
                        RETWEET_LOG_FILE,
                        f"[ERROR] リツイート失敗: {tid} / {e}",
                    )
                    log_message(
                        RETWEET_LOG_FILE,
                        traceback.format_exc(),
                    )

        save_retweeted_list(retweeted_ids)
        log_message(
            RETWEET_LOG_FILE,
            f"=== リツイート完了 [{account_label}]（{total_retweets}件） ===",
        )
        run_status = "completed"

    except Exception as e:
        run_status = "error"
        log_message(
            RETWEET_LOG_FILE,
            f"[FATAL] 例外内容: {e}",
        )
        log_message(
            RETWEET_LOG_FILE,
            "[FATAL] Traceback:\n" + traceback.format_exc(),
        )
        _finish_observation(recorder, run_status)
        sys.exit(1)

    finally:
        if run_status != "error":
            _finish_observation(recorder, run_status)


def _finish_observation(recorder: RunRecorder | None, status: str) -> None:
    """
    観測記録を保存し、概要をログに出す。

    記録の保存に失敗しても本番処理（リツイート）には影響させない。
    """
    if recorder is None:
        return
    try:
        path = recorder.finish(status)
        summary = recorder.summary()
        log_message(RETWEET_LOG_FILE, f"[OBSERVATION] 保存先: {path}")
        log_message(RETWEET_LOG_FILE, f"[OBSERVATION] 概要: {summary}")
    except Exception as e:  # noqa: BLE001
        log_message(RETWEET_LOG_FILE, f"[WARN] 観測記録の保存に失敗: {e}")



# ------------------------------------------------------------
# エントリポイント
# ------------------------------------------------------------
if __name__ == "__main__":
    # 従来互換：サフィックスなし・config.KEYWORDS を使用
    run_retweet_task()
