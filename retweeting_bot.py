"""
retweeting_bot.py
======================
フォロー対象ユーザー（username）とキーワードに基づいて
新規の「歌ってみた」動画の告知ツイートを検索し、自動でリツイートするバッチジョブ。

処理の流れ（Phase 1・2）:
  1. 前回の実行で翌日に回した候補（pending.json）を先に処理する
  2. 前回検索した終わりの時刻〜本日 0:00（JST）を、全クエリで全件検索する
  3. 取得後に判定する（ショート・配信・リツイート済みの動画・同じ動画の重複を除外）
  4. 投稿時刻の早い順にリツイートし、上限を超えた分は pending.json に回す
  5. リツイート済み ID・動画 ID・実行状態・観測記録を保存する

本モジュールは「1 Twitter アカウント分のリツイート処理」を
1 タスクとして完結させる設計を採用している。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Sequence, Tuple

from config import (
    EXCLUDE_KEYWORDS,
    KEYWORDS,
    MAX_LOOKBACK_DAYS,
    MAX_PAGES_PER_SEARCH,
    MAX_TWEETS_PER_SEARCH,
    MAX_USERS_PER_SEARCH,
    RETWEET_LIMIT,
)

from tools.core import (
    log_message,
    get_search_window_utc,
    RETWEET_LOG_FILE,
)

from tools.data_manager import (
    load_following_list,
    load_pending,
    load_retweeted_list,
    load_retweeted_videos,
    load_state,
    save_pending,
    save_retweeted_list,
    save_retweeted_videos,
    save_state,
)

from tools.twitter_api import (
    create_client,
    build_search_query,
    search_all_tweets,
    retweet,
)

from tools.observation import RunRecorder
from tools.video_filter import Judgement, select_candidates


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
        queries.append((chunk, query))

    return queries


# ------------------------------------------------------------
# キーワード定義の選択
# ------------------------------------------------------------
def resolve_keywords(suffix: str | None) -> Tuple[Sequence[str], str]:
    """suffix に対応するキーワード定義（config.KEYWORDS / KEYWORDS_<suffix>）を返す。"""
    if suffix is None:
        return KEYWORDS, "KEYWORDS"
    name = f"KEYWORDS_{suffix}"
    try:
        return getattr(sys.modules["config"], name), name
    except AttributeError:
        raise ValueError(f"config.py に {name} が定義されていません。")


# ------------------------------------------------------------
# 1アカウント分のリツイートタスク
# ------------------------------------------------------------
def run_retweet_task(suffix: str | None = None, dry_run: bool = False) -> None:
    """
    指定された Twitter アカウント（suffix）で、新規の告知ツイートを検索・判定し、
    リツイートする。

    Args:
        suffix (str | None):
            使用する Twitter アカウントの識別子。
            None の場合はサフィックスなし（KEYWORDS）を使用する。
        dry_run (bool):
            True の場合、検索と判定だけを行い、リツイートもファイルの保存もしない。
            （検索の読み取り費用はかかる）

    Raises:
        SystemExit:
            致命的な例外発生時に異常終了する。
    """
    account_label = suffix or "DEFAULT"
    mode = "（dry-run: リツイート・保存なし）" if dry_run else ""
    log_message(RETWEET_LOG_FILE, f"=== リツイートジョブ開始 [{account_label}]{mode} ===")

    recorder: RunRecorder | None = None
    run_status = "error"

    try:
        keywords, keywords_name = resolve_keywords(suffix)
        log_message(
            RETWEET_LOG_FILE,
            f"[DEBUG] 使用キーワード定義: {keywords_name} = {list(keywords)}",
        )

        client = create_client(suffix)

        # ----------------------------------------------------
        # 保存済みデータの読み込み
        # ----------------------------------------------------
        retweeted_ids = load_retweeted_list()
        retweeted_videos = load_retweeted_videos()
        pending = load_pending()
        state = load_state()

        # ----------------------------------------------------
        # 検索期間（前回の続き〜本日 0:00 JST）
        # ----------------------------------------------------
        last_until_str = state.get("last_searched_until_utc")
        last_until = datetime.fromisoformat(last_until_str) if last_until_str else None
        window = get_search_window_utc(last_until, max_lookback_days=MAX_LOOKBACK_DAYS)
        start_time, end_time = window if window else (None, None)
        log_message(RETWEET_LOG_FILE, f"[DEBUG] 検索期間: {start_time} 〜 {end_time}")

        recorder = RunRecorder(account_label, start_time, end_time, keywords)

        # ----------------------------------------------------
        # 検索（全クエリ・全ページ）
        # ----------------------------------------------------
        collected: List[Any] = []
        if window is None:
            log_message(RETWEET_LOG_FILE, "本日分は検索済みのため、検索を省略します。")
        else:
            queries = create_search_queries(keywords)
            recorder.set_total_queries(len(queries))
            for chunk, query in queries:
                tweets, meta = search_all_tweets(
                    client=client,
                    query=query,
                    start_time=start_time,
                    end_time=end_time,
                    page_size=MAX_TWEETS_PER_SEARCH,
                    max_pages=MAX_PAGES_PER_SEARCH,
                )
                log_message(
                    RETWEET_LOG_FILE,
                    f"[DEBUG] 取得 {meta['result_count']} 件 / {meta['pages']} ページ"
                    + ("（ページ上限で打ち切り）" if meta["next_token"] else ""),
                )
                recorder.add_search(query, chunk, meta, tweets)
                collected.extend(tweets)

        # ----------------------------------------------------
        # 判定（ショート・配信・リツイート済み動画・重複を除外）
        # ----------------------------------------------------
        accepted, rejected = select_candidates(
            collected,
            retweeted_ids=retweeted_ids,
            retweeted_videos=set(retweeted_videos),
            exclude_keywords=EXCLUDE_KEYWORDS,
        )
        for j in rejected:
            recorder.set_decision(j.tweet_id, f"rejected:{j.reason}")
            recorder.set_video(j.tweet_id, j.video_id)
        for j in accepted:
            recorder.set_video(j.tweet_id, j.video_id)
        log_message(
            RETWEET_LOG_FILE,
            f"判定結果: 取得 {len(set(str(t.id) for t in collected))} 件 → "
            f"採用 {len(accepted)} 件 / 除外 {len(rejected)} 件",
        )
        if dry_run:
            _print_judgements(accepted, rejected)

        # ----------------------------------------------------
        # リツイート（持ち越し分 → 今回分の順。上限超過分は翌日へ）
        # ----------------------------------------------------
        queue: List[Tuple[str, Dict[str, Any], Judgement | None]] = []
        for entry in pending:
            queue.append(("pending", entry, None))
        for j in accepted:
            entry = {
                "tweet_id": j.tweet_id,
                "video_ids": j.video_ids,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            queue.append(("new", entry, j))

        next_pending: List[Dict[str, Any]] = []
        total_retweets = 0

        for source, entry, j in queue:
            tid = str(entry["tweet_id"])
            video_ids = [str(v) for v in entry.get("video_ids", [])]

            def record(decision: str, detail: str | None = None) -> None:
                if source == "pending":
                    recorder.add_pending_result(entry, decision, detail)
                else:
                    recorder.set_decision(tid, decision, detail)

            # 持ち越し分は、その後に同じ動画をリツイートしていないかを確認し直す
            if tid in retweeted_ids or any(v in retweeted_videos for v in video_ids):
                record("rejected:video_already_retweeted")
                continue

            if total_retweets >= RETWEET_LIMIT:
                next_pending.append(entry)
                record("pending")
                continue

            if dry_run:
                record("would_retweet")
                total_retweets += 1
                for v in video_ids:
                    retweeted_videos[v] = tid
                continue

            try:
                retweet(client, tid)
                retweeted_ids.add(tid)
                for v in video_ids:
                    retweeted_videos[v] = tid
                total_retweets += 1
                record("retweeted")
                preview = ""
                if j is not None and getattr(j.tweet, "text", None):
                    preview = j.tweet.text.replace("\n", " ")[:50]
                log_message(
                    RETWEET_LOG_FILE,
                    f"リツイート成功: {tid} / video={video_ids[:1]} / {preview}...",
                )
            except Exception as e:  # noqa: BLE001
                record("failed", detail=str(e)[:200])
                log_message(RETWEET_LOG_FILE, f"[ERROR] リツイート失敗: {tid} / {e}")
                log_message(RETWEET_LOG_FILE, traceback.format_exc())

        if next_pending:
            log_message(
                RETWEET_LOG_FILE,
                f"リツイート上限（{RETWEET_LIMIT} 件）に達したため、{len(next_pending)} 件を翌日に回します。",
            )

        # ----------------------------------------------------
        # 保存
        # ----------------------------------------------------
        if not dry_run:
            save_retweeted_list(retweeted_ids)
            save_retweeted_videos(retweeted_videos)
            save_pending(next_pending)
            if end_time is not None:
                state["last_searched_until_utc"] = end_time.isoformat()
                save_state(state)

        log_message(
            RETWEET_LOG_FILE,
            f"=== リツイート完了 [{account_label}]（{total_retweets}件）{mode} ===",
        )
        run_status = "completed" if not dry_run else "dry_run"

    except Exception as e:  # noqa: BLE001
        run_status = "error"
        log_message(RETWEET_LOG_FILE, f"[FATAL] 例外内容: {e}")
        log_message(RETWEET_LOG_FILE, "[FATAL] Traceback:\n" + traceback.format_exc())
        _finish_observation(recorder, run_status, save=not dry_run)
        sys.exit(1)

    finally:
        if run_status != "error":
            _finish_observation(recorder, run_status, save=not dry_run)


def _print_judgements(accepted: List[Judgement], rejected: List[Judgement]) -> None:
    """dry-run 用: 判定結果を 1 件ずつ画面に表示する（本文は先頭だけ。ファイルには残さない）。"""
    jst = timezone(timedelta(hours=9))

    def line(j: Judgement, label: str) -> str:
        when = j.created_at.astimezone(jst).strftime("%m/%d %H:%M") if j.created_at else "--"
        text = (getattr(j.tweet, "text", "") or "").replace("\n", " ")[:60]
        return (
            f"  [{label}] {when} https://x.com/i/status/{j.tweet_id}"
            f" video={j.video_id or '-'}\n        {text}"
        )

    print("\n--- リツイートする予定（投稿時刻の早い順） ---")
    for j in accepted:
        print(line(j, "採用"))
    print("\n--- 除外 ---")
    for j in rejected:
        print(line(j, j.reason))
    print()


def _finish_observation(recorder: RunRecorder | None, status: str, save: bool = True) -> None:
    """
    観測記録を保存し、概要をログに出す。

    記録の保存に失敗しても本番処理（リツイート）には影響させない。
    dry-run の場合は保存せず、概要だけを表示する。
    """
    if recorder is None:
        return
    try:
        if save:
            path = recorder.finish(status)
            log_message(RETWEET_LOG_FILE, f"[OBSERVATION] 保存先: {path}")
        log_message(RETWEET_LOG_FILE, f"[OBSERVATION] 概要: {recorder.summary()}")
    except Exception as e:  # noqa: BLE001
        log_message(RETWEET_LOG_FILE, f"[WARN] 観測記録の保存に失敗: {e}")


# ------------------------------------------------------------
# エントリポイント
# ------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="歌ってみた告知の自動リツイート")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="検索と判定だけを行い、リツイートもファイル保存もしない（検索の読み取り費用はかかる）",
    )
    args = parser.parse_args()
    # 従来互換：サフィックスなし・config.KEYWORDS を使用
    run_retweet_task(dry_run=args.dry_run)
