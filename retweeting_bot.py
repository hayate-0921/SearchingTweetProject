"""
retweeting_bot.py
======================
フォロー対象ユーザー（username）とキーワードに基づいて
新規の「歌ってみた」動画の告知ツイートを検索し、自動でリツイートするバッチジョブ。

処理の流れ（Phase 1〜3）:
  1. 前回の実行で翌日に回した候補（pending.json）を先に処理する
  2. 前回検索した終わりの時刻〜本日 0:00（JST）を、全クエリで全件検索する
  3. YouTube の RSS で見つけた歌ってみたのうち、2 で告知が見つからなかった動画について、
     X で本人の告知を探し直す（Phase 3。tools/youtube_fallback.py）
  4. 取得後に判定する（ショート・配信・リツイート済みの動画・同じ動画の重複を除外）
  5. 投稿時刻の早い順にリツイートし、上限を超えた分は pending.json に回す
  6. リツイート済み ID・動画 ID・実行状態・監視中の動画・観測記録を保存する
  7. 対象者・歌ってみた動画・リツイートした告知・実行の概要を data/bot.db に記録する
     （tools/database.py。記録に失敗してもリツイートには影響しない）

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
    YOUTUBE_ANNOUNCE_LOOKBACK_DAYS,
    YOUTUBE_COVER_TITLE_PATTERN,
    YOUTUBE_EXCLUDE_TITLE_PATTERN,
    YOUTUBE_FALLBACK_ENABLED,
    YOUTUBE_FEED_TIMEOUT,
    YOUTUBE_FEED_WORKERS,
    YOUTUBE_FULL_SCAN_AFTER_DAYS,
    YOUTUBE_FULL_SCAN_MAX_POSTS,
    YOUTUBE_MAX_USERS_PER_RUN,
    YOUTUBE_WATCH_DAYS,
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
    load_youtube_channels,
    load_youtube_watch,
    save_pending,
    save_retweeted_list,
    save_retweeted_videos,
    save_state,
    save_youtube_watch,
)

from tools.twitter_api import (
    create_client,
    build_search_query,
    search_all_tweets,
    retweet,
)

from tools import database
from tools.observation import RunRecorder
from tools.video_filter import Judgement, select_candidates
from tools import youtube_fallback as yf
from tools.youtube_feed import fetch_feeds, is_cover_title


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
    # データベースに記録する内容（歌ってみた動画・リツイートした告知）
    ledger: Dict[str, List[Dict[str, Any]]] = {"cover_videos": [], "retweets": []}

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
        # Phase 3: YouTube を起点に、取りこぼした告知を探し直す
        # ----------------------------------------------------
        youtube_watch = None
        if window is not None and YOUTUBE_FALLBACK_ENABLED:
            youtube_watch, found, cover_videos = run_youtube_fallback(
                client, recorder, collected, set(retweeted_videos), end_time
            )
            collected.extend(found)
            ledger["cover_videos"] = cover_videos

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
            author_id = getattr(j.tweet, "author_id", None)
            entry = {
                "tweet_id": j.tweet_id,
                "video_ids": j.video_ids,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "author_id": str(author_id) if author_id else None,
                "found_by": _found_by(recorder.source_of(j.tweet_id)),
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
                if video_ids:
                    ledger["retweets"].append(
                        {
                            "tweet_id": tid,
                            "video_id": video_ids[0],
                            "author_id": entry.get("author_id"),
                            "found_by": entry.get("found_by"),
                            "retweeted_at": datetime.now(timezone.utc),
                        }
                    )
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
            if youtube_watch is not None:
                # リツイートした動画は監視リストから外す（期限切れは次回の更新で外れる）
                for v in list(youtube_watch):
                    if v in retweeted_videos:
                        del youtube_watch[v]
                save_youtube_watch(youtube_watch)
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
        if not dry_run:
            _record_database(recorder, ledger)
        sys.exit(1)

    finally:
        if run_status != "error":
            _finish_observation(recorder, run_status, save=not dry_run)
            if not dry_run:
                _record_database(recorder, ledger)


def run_youtube_fallback(
    client: Any,
    recorder: RunRecorder,
    collected: List[Any],
    retweeted_videos: set,
    end_time: datetime,
) -> Tuple[Dict[str, Dict[str, Any]] | None, List[Any], List[Dict[str, Any]]]:
    """
    Phase 3: YouTube の RSS で歌ってみたを見つけ、通常の検索で告知が見つからなかった動画について
    X で本人の告知を探す。

    YouTube や X の一時的な失敗で本番処理（通常のリツイート）を止めないよう、
    例外はここで受け止めてログに残す。

    Returns:
        (更新後の監視リスト（保存は呼び出し側）, 見つかった告知の投稿, RSS で見つけた歌ってみた)
        対応表がない・失敗した場合は (None, [], [])。
        3 つめはデータベースへの記録用（期限内・ショート以外・タイトルが歌ってみた）。
    """
    try:
        channels = load_youtube_channels()
        if not channels:
            log_message(RETWEET_LOG_FILE, "[YOUTUBE] data/youtube_channels.json がないため省略します。")
            return None, [], []

        now = datetime.now(timezone.utc)
        watch = load_youtube_watch()
        channel_to_user = {cid: user for user, cid in channels.items()}

        videos, errors = fetch_feeds(
            channel_to_user.keys(), workers=YOUTUBE_FEED_WORKERS, timeout=YOUTUBE_FEED_TIMEOUT
        )
        added = yf.update_watch(
            watch,
            videos,
            channel_to_user,
            retweeted_videos,
            now,
            YOUTUBE_COVER_TITLE_PATTERN,
            YOUTUBE_EXCLUDE_TITLE_PATTERN,
            YOUTUBE_WATCH_DAYS,
        )

        limit = now - timedelta(days=YOUTUBE_WATCH_DAYS)
        cover_videos = [
            {
                "video_id": v.video_id,
                "x_username": channel_to_user[v.channel_id],
                "channel_id": v.channel_id,
                "title": v.title,
                "published_at": v.published,
            }
            for v in videos
            if not v.is_short
            and v.published >= limit
            and v.channel_id in channel_to_user
            and is_cover_title(v.title, YOUTUBE_COVER_TITLE_PATTERN, YOUTUBE_EXCLUDE_TITLE_PATTERN)
        ]

        # 通常の検索で告知が見つかった動画は探さない
        handled = set()
        for tw in collected:
            handled |= yf.eligible_video_ids(tw)

        plans, skipped = yf.plan_searches(
            watch,
            handled,
            end_time,
            now,
            YOUTUBE_ANNOUNCE_LOOKBACK_DAYS,
            YOUTUBE_MAX_USERS_PER_RUN,
        )

        def search_fn(**kwargs: Any) -> Tuple[List[Any], dict]:
            return search_all_tweets(client=client, **kwargs)

        found, logs = yf.find_announcements(
            plans, search_fn, YOUTUBE_FULL_SCAN_MAX_POSTS, YOUTUBE_FULL_SCAN_AFTER_DAYS
        )
        for lg in logs:
            recorder.add_search(lg.query, [lg.username], lg.meta, lg.matched, source=f"youtube:{lg.step}")

        yf.mark_searched(watch, plans)
        for tw in found:
            handled |= yf.eligible_video_ids(tw)
        yf.mark_found(watch, handled)

        found_videos = sorted({v for p in plans for v in p.found})
        recorder.set_youtube(
            {
                "channels": len(channel_to_user),
                "feed_errors": len(errors),
                "feed_videos": len(videos),
                "added_to_watch": added,
                "watching": len([v for v, i in watch.items() if i.get("status") != "found"]),
                "searched_users": [p.username for p in plans],
                "skipped_users": skipped,
                "found_videos": found_videos,
            }
        )
        log_message(
            RETWEET_LOG_FILE,
            f"[YOUTUBE] RSS {len(channel_to_user)} チャンネル（失敗 {len(errors)}）/ 新しい歌ってみた {len(added)} 本 / "
            f"告知を探したタレント {len(plans)} 人 / 見つかった動画 {len(found_videos)} 本",
        )
        if errors:
            log_message(RETWEET_LOG_FILE, f"[YOUTUBE] RSS の取得に失敗: {list(errors.items())[:5]}")
        if skipped:
            log_message(RETWEET_LOG_FILE, f"[YOUTUBE] 上限のため次回に回したタレント: {skipped}")
        return watch, found, cover_videos

    except Exception as e:  # noqa: BLE001
        log_message(RETWEET_LOG_FILE, f"[WARN] YouTube を起点にした検索に失敗しました（通常の処理は続けます）: {e}")
        log_message(RETWEET_LOG_FILE, traceback.format_exc(), print_to_console=False)
        return None, [], []


def _found_by(source: str | None) -> str | None:
    """観測記録の検索の種類を、データベースの found_by（keyword / youtube_url / full_scan）にする。"""
    if not source:
        return None
    return source.split(":", 1)[-1]


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


def _record_database(recorder: RunRecorder | None, ledger: Dict[str, List[Dict[str, Any]]]) -> None:
    """
    実行の記録を data/bot.db に保存する（_finish_observation の後に呼ぶ）。

    記録用のため、失敗してもリツイートや JSON の保存には影響させない。
    """
    if recorder is None or "status" not in recorder.run:
        return
    try:
        result = database.record_execution(
            run=recorder.run,
            summary=recorder.run.get("summary") or recorder.summary(),
            following=load_following_list(),
            channels=load_youtube_channels(),
            cover_videos=ledger.get("cover_videos", []),
            retweets=ledger.get("retweets", []),
        )
        log_message(
            RETWEET_LOG_FILE,
            f"[DATABASE] run_id={result['run_id']} / 新しい歌ってみた {result['new_cover_videos']} 本 / "
            f"告知 {result['new_announce_tweets']} 件を記録",
        )
    except Exception as e:  # noqa: BLE001
        log_message(RETWEET_LOG_FILE, f"[WARN] データベースへの記録に失敗: {e}")
        log_message(RETWEET_LOG_FILE, traceback.format_exc(), print_to_console=False)


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
