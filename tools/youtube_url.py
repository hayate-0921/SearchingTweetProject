"""
youtube_url.py
---------------
YouTube の URL から動画 ID と URL の種類を取り出すユーティリティ。

API 呼び出しは行わない純粋関数のみを提供する。
Phase 0（計測）では観測記録の作成に、Phase 2 以降では
動画 ID 単位の重複判定・ショート判定に使用する想定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List
from urllib.parse import parse_qs, urlparse

# YouTube の動画 ID は英数字・ハイフン・アンダースコアの 11 文字
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}


@dataclass(frozen=True)
class YouTubeVideoRef:
    """
    URL から取り出した動画への参照。

    Attributes:
        video_id (str): 11 文字の動画 ID。
        kind (str):
            URL の種類。以下のいずれか。
              - "watch"   : youtube.com/watch?v=ID
              - "shorts"  : youtube.com/shorts/ID（ショート動画で確定）
              - "youtu.be": youtu.be/ID（ショートかどうかは URL からは不明）
              - "live"    : youtube.com/live/ID（配信・プレミア）
              - "embed"   : youtube.com/embed/ID
        url (str): 元の URL。
    """

    video_id: str
    kind: str
    url: str


def is_youtube_url(url: str) -> bool:
    """URL のホストが YouTube（youtu.be を含む）であれば True を返す。"""
    host = (urlparse(url).hostname or "").lower()
    return host in _YOUTUBE_HOSTS or host in _SHORT_HOSTS


def parse_youtube_url(url: str) -> YouTubeVideoRef | None:
    """
    YouTube の URL を解析し、動画 ID と URL の種類を返す。

    チャンネルページや再生リストなど、特定の動画を指さない URL の場合は None を返す。

    Args:
        url (str): 展開済みの URL（t.co ではなく expanded_url を渡すこと）。

    Returns:
        YouTubeVideoRef | None
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    segments = [s for s in parsed.path.split("/") if s]

    candidate: str | None = None
    kind = ""

    if host in _SHORT_HOSTS:
        if segments:
            candidate, kind = segments[0], "youtu.be"
    elif host in _YOUTUBE_HOSTS:
        if segments[:1] == ["watch"]:
            candidate = (parse_qs(parsed.query).get("v") or [None])[0]
            kind = "watch"
        elif len(segments) >= 2 and segments[0] in ("shorts", "live", "embed"):
            candidate, kind = segments[1], segments[0]

    if candidate and _VIDEO_ID_RE.match(candidate):
        return YouTubeVideoRef(video_id=candidate, kind=kind, url=url)
    return None


def extract_video_refs(urls: Iterable[str]) -> List[YouTubeVideoRef]:
    """
    URL の列から YouTube 動画への参照を重複なく取り出す。

    同じ動画 ID が複数回現れた場合は、最初に現れたものを採用する。
    ただし "shorts" 形式が含まれていれば、ショートであることが確定するため
    kind は "shorts" を優先する。
    """
    refs: dict[str, YouTubeVideoRef] = {}
    for url in urls:
        ref = parse_youtube_url(url)
        if ref is None:
            continue
        existing = refs.get(ref.video_id)
        if existing is None or (ref.kind == "shorts" and existing.kind != "shorts"):
            refs[ref.video_id] = ref
    return list(refs.values())
