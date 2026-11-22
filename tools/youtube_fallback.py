"""
youtube_fallback.py
--------------------
Phase 3: YouTube を起点にした取りこぼし対策。

通常の検索（キーワード + YouTube リンク）では、次のような告知を取りこぼす。
  - 本文に検索ワードがない（例:「24時からプレミア公開！」と曲名だけ）
  - X 側で URL が検索の索引に入らず、url: 条件に当たらない

そこで、タレントの YouTube チャンネルの RSS から歌ってみたを見つけ、
通常の検索で告知が見つからなかった動画について、X で本人の投稿を探し直す。

探し方（タレントごと）:
  1. from:本人 (url:"youtube.com" OR url:"youtu.be")   … YouTube リンクを含む投稿だけ読む（安い）
  2. 1 で見つからなかった動画があれば from:本人 を読む … 上限 YOUTUBE_FULL_SCAN_MAX_POSTS 件
どちらも、対象の動画 ID を含む投稿だけを候補にする（無関係な投稿はリツイートしない）。

同じ投稿を何度も読まないよう、動画ごとに「どこまで探したか」を data/youtube_watch.json に残し、
次の実行ではその続きだけを探す。公開から YOUTUBE_WATCH_DAYS 日たった動画は探すのをやめる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .video_filter import ELIGIBLE_KINDS, expanded_urls
from .youtube_feed import FeedVideo, is_cover_title
from .youtube_url import extract_video_refs

# X の検索 API で遡れる上限（7 日）より少し内側にする
_SEARCH_API_MARGIN = timedelta(minutes=5)


def eligible_video_ids(tweet: Any) -> Set[str]:
    """投稿に含まれる、リツイート対象になりうる動画 ID（ショート・配信を除く）。"""
    return {r.video_id for r in extract_video_refs(expanded_urls(tweet)) if r.kind in ELIGIBLE_KINDS}


# ----------------------------------------------------------------------
# 監視リストの更新
# ----------------------------------------------------------------------
def update_watch(
    watch: Dict[str, Dict[str, Any]],
    videos: Iterable[FeedVideo],
    channel_to_user: Dict[str, str],
    retweeted_videos: Set[str],
    now: datetime,
    cover_pattern: str,
    exclude_pattern: str,
    watch_days: int,
) -> List[str]:
    """
    RSS の動画から歌ってみたを監視リストに加え、期限切れ・リツイート済みを外す。

    Returns:
        新しく加えた動画 ID のリスト。
    """
    limit = now - timedelta(days=watch_days)

    for vid in list(watch):
        info = watch[vid]
        published = datetime.fromisoformat(info["published"])
        if vid in retweeted_videos or published < limit:
            del watch[vid]

    added: List[str] = []
    for v in videos:
        if v.is_short or v.published < limit or v.published > now + timedelta(days=30):
            continue
        if v.video_id in watch or v.video_id in retweeted_videos:
            continue
        user = channel_to_user.get(v.channel_id)
        if not user or not is_cover_title(v.title, cover_pattern, exclude_pattern):
            continue
        watch[v.video_id] = {
            "username": user,
            "channel_id": v.channel_id,
            "published": v.published.isoformat(),
            "added_at": now.isoformat(),
            "searched_until_utc": None,
        }
        added.append(v.video_id)
    return added


# ----------------------------------------------------------------------
# 検索計画
# ----------------------------------------------------------------------
@dataclass
class UserSearch:
    """1 人分の告知探し。"""

    username: str
    video_ids: List[str]
    start: datetime
    end: datetime
    latest_published: Optional[datetime] = None  # 対象の動画のうち、最も遅い公開日時
    found: Dict[str, str] = field(default_factory=dict)  # 動画 ID → 見つかった投稿 ID


def plan_searches(
    watch: Dict[str, Dict[str, Any]],
    handled_videos: Set[str],
    end: datetime,
    now: datetime,
    announce_lookback_days: int,
    max_users: int,
) -> Tuple[List[UserSearch], List[str]]:
    """
    監視中の動画を、タレントごとの検索にまとめる。

    Args:
        handled_videos: 通常の検索で告知が見つかった動画（探さない）。
        end: 検索の終わり（通常の検索と同じ時刻）。

    Returns:
        (検索のリスト, 上限を超えて今回は見送ったタレント)
    """
    earliest = now - timedelta(days=7) + _SEARCH_API_MARGIN
    per_user: Dict[str, List[Tuple[str, datetime]]] = {}

    for vid, info in watch.items():
        if vid in handled_videos or info.get("status") == "found":
            continue
        if info.get("searched_until_utc"):
            start = datetime.fromisoformat(info["searched_until_utc"])
        else:
            start = datetime.fromisoformat(info["published"]) - timedelta(days=announce_lookback_days)
        start = max(start, earliest)
        if start >= end:
            continue
        per_user.setdefault(info["username"], []).append((vid, start))

    # 公開の早い動画を持つタレントから順に探す
    ordered = sorted(
        per_user.items(),
        key=lambda kv: min(datetime.fromisoformat(watch[v]["published"]) for v, _ in kv[1]),
    )
    plans = [
        UserSearch(
            username=user,
            video_ids=sorted(v for v, _ in items),
            start=min(s for _, s in items),
            end=end,
            latest_published=max(datetime.fromisoformat(watch[v]["published"]) for v, _ in items),
        )
        for user, items in ordered
    ]
    return plans[:max_users], [p.username for p in plans[max_users:]]


# ----------------------------------------------------------------------
# X で告知を探す
# ----------------------------------------------------------------------
SearchFn = Callable[..., Tuple[List[Any], dict]]


@dataclass
class SearchLog:
    """観測記録用: 検索 1 回分。"""

    query: str
    username: str
    meta: dict
    matched: List[Any]
    step: str  # "youtube_url" または "full_scan"


def find_announcements(
    plans: List[UserSearch],
    search_fn: SearchFn,
    full_scan_max_posts: int,
    full_scan_after_days: int = 1,
) -> Tuple[List[Any], List[SearchLog]]:
    """
    計画どおりに X を検索し、対象の動画を含む本人の投稿を集める。

    Args:
        search_fn: tools.twitter_api.search_all_tweets と同じ引数をとる関数
                   （client は呼び出し側で部分適用しておく）。

    Returns:
        (対象の動画を含む投稿, 検索ログ)

    全件読み（2 段目）は、期間の終わりを「公開日時 + full_scan_after_days」までに絞る。
    結果は新しい順に返るため、期間が長いと古い告知が上限から漏れるのを防ぐ。
    """
    matched_all: List[Any] = []
    logs: List[SearchLog] = []

    for plan in plans:
        targets = set(plan.video_ids)
        full_end = plan.end
        if plan.latest_published is not None:
            full_end = min(plan.end, plan.latest_published + timedelta(days=full_scan_after_days))
        steps = [
            ("youtube_url", f'from:{plan.username} (url:"youtube.com" OR url:"youtu.be") -is:retweet -is:reply',
             plan.end, 100, 3),
            ("full_scan", f"from:{plan.username} -is:retweet -is:reply",
             full_end, min(100, full_scan_max_posts), max(1, full_scan_max_posts // 100)),
        ]
        for step, query, step_end, page_size, max_pages in steps:
            if not targets - set(plan.found):
                break
            if plan.start >= step_end:
                continue
            tweets, meta = search_fn(
                query=query,
                start_time=plan.start,
                end_time=step_end,
                page_size=max(10, page_size),
                max_pages=max_pages,
            )
            matched = []
            for tw in tweets:
                hit = eligible_video_ids(tw) & targets
                if not hit:
                    continue
                matched.append(tw)
                for v in hit:
                    plan.found.setdefault(v, str(tw.id))
            matched_all.extend(matched)
            logs.append(SearchLog(query=query, username=plan.username, meta=meta, matched=matched, step=step))

    return matched_all, logs


def mark_searched(watch: Dict[str, Dict[str, Any]], plans: List[UserSearch]) -> None:
    """探した範囲を監視リストに記録する（次の実行ではその続きから探す）。"""
    for plan in plans:
        for vid in plan.video_ids:
            if vid in watch:
                watch[vid]["searched_until_utc"] = plan.end.isoformat()


def mark_found(watch: Dict[str, Dict[str, Any]], handled_videos: Set[str]) -> None:
    """
    告知が見つかった（＝通常の判定に回した）動画に印を付け、以後は探さない。

    監視リストからすぐに消すと、判定で除外された動画（除外ワードを含むなど）が
    翌日の RSS で再び加わり、毎日探し直すことになるため、期限まで残しておく。
    """
    for vid in handled_videos:
        if vid in watch:
            watch[vid]["status"] = "found"
