"""
video_filter.py
----------------
検索で集めた投稿から「リツイートすべき投稿」を選ぶ判定ロジック（Phase 2）。

API 呼び出しやファイル入出力は行わない純粋関数のみを提供する。
判定に使うのは、投稿の展開済み URL（entities.urls）と本文だけ。

判定の順序（最初に当てはまった理由で除外する）:
  1. already_retweeted       : そのツイート自体をリツイート済み
  2. shorts                  : youtube.com/shorts/ のリンクを含む（ショート動画の告知）
  3. live                    : 通常の動画リンクがなく、youtube.com/live/ のリンクだけ（配信の告知）
  4. no_youtube_video        : 特定の動画を指す YouTube リンクがない（チャンネルページ等）
  5. exclude_keyword         : 本文に除外ワードを含む（検索条件の除外をすり抜けた場合の保険）
  6. video_already_retweeted : 同じ動画を過去にリツイート済み
  7. duplicate_in_run        : 同じ実行の中で、同じ動画のより早い投稿がある
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .youtube_url import YouTubeVideoRef, extract_video_refs

# リツイート対象になりうる URL の種類（shorts と live は除く）
ELIGIBLE_KINDS = {"watch", "youtu.be", "embed"}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def expanded_urls(tweet: Any) -> List[str]:
    """Tweet の entities.urls から展開済み URL を取り出す。"""
    entities = getattr(tweet, "entities", None) or {}
    urls: List[str] = []
    for u in entities.get("urls", []) or []:
        url = u.get("unwound_url") or u.get("expanded_url") or u.get("url")
        if url:
            urls.append(url)
    return urls


@dataclass
class Judgement:
    """1 件の投稿に対する判定結果。"""

    tweet_id: str
    created_at: Optional[datetime]
    accepted: bool
    reason: str  # accepted の場合は "accepted"
    video_ids: List[str] = field(default_factory=list)  # リツイート対象の動画 ID（先頭が代表）
    tweet: Any = None

    @property
    def video_id(self) -> Optional[str]:
        return self.video_ids[0] if self.video_ids else None


def judge_tweet(
    tweet: Any,
    retweeted_ids: Set[str],
    retweeted_videos: Set[str],
    exclude_keywords: Sequence[str],
) -> Judgement:
    """
    投稿 1 件を判定する（同じ実行内での重複は select_candidates で判定する）。
    """
    tid = str(tweet.id)
    created_at = getattr(tweet, "created_at", None)
    text = (getattr(tweet, "text", "") or "").lower()

    def result(reason: str, video_ids: Iterable[str] = ()) -> Judgement:
        return Judgement(
            tweet_id=tid,
            created_at=created_at,
            accepted=reason == "accepted",
            reason=reason,
            video_ids=list(video_ids),
            tweet=tweet,
        )

    if tid in retweeted_ids:
        return result("already_retweeted")

    refs: List[YouTubeVideoRef] = extract_video_refs(expanded_urls(tweet))
    eligible = [r.video_id for r in refs if r.kind in ELIGIBLE_KINDS]

    if any(r.kind == "shorts" for r in refs):
        return result("shorts", [r.video_id for r in refs])
    if not eligible:
        if any(r.kind == "live" for r in refs):
            return result("live", [r.video_id for r in refs])
        return result("no_youtube_video")
    if any(w.lower() in text for w in exclude_keywords):
        return result("exclude_keyword", eligible)
    if any(v in retweeted_videos for v in eligible):
        return result("video_already_retweeted", eligible)
    return result("accepted", eligible)


def select_candidates(
    tweets: Iterable[Any],
    retweeted_ids: Set[str],
    retweeted_videos: Set[str],
    exclude_keywords: Sequence[str],
) -> Tuple[List[Judgement], List[Judgement]]:
    """
    投稿の集まりを判定し、(採用, 除外) の組を返す。

    - 同じツイート ID が複数回現れても 1 回だけ判定する
    - 投稿時刻の早い順に判定し、同じ動画の 2 件目以降は duplicate_in_run として除外する
      （＝「最初に見つけた告知を採用」する）
    - 採用リストは投稿時刻の早い順に並ぶ
    """
    unique: Dict[str, Any] = {}
    for tw in tweets:
        unique.setdefault(str(tw.id), tw)

    ordered = sorted(
        unique.values(),
        key=lambda t: (getattr(t, "created_at", None) or _EPOCH, str(t.id)),
    )

    accepted: List[Judgement] = []
    rejected: List[Judgement] = []
    seen_videos: Set[str] = set()

    for tw in ordered:
        j = judge_tweet(tw, retweeted_ids, retweeted_videos, exclude_keywords)
        if j.accepted and any(v in seen_videos for v in j.video_ids):
            j.accepted = False
            j.reason = "duplicate_in_run"
        if j.accepted:
            seen_videos.update(j.video_ids)
            accepted.append(j)
        else:
            rejected.append(j)
    return accepted, rejected
