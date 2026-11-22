"""
youtube_feed.py
----------------
Phase 3: YouTube チャンネルの RSS から最新の動画を取得し、歌ってみたを見分ける。

RSS（https://www.youtube.com/feeds/videos.xml?channel_id=...）は API キー不要・無料で、
チャンネルごとに最新 15 本の動画（ショート・配信のアーカイブを含む）を返す。

注意:
  - ショートはリンクが /shorts/ になるため、URL で見分けられる
  - プレミア公開の動画は、published がアップロードした時刻になり、
    実際の公開時刻とずれることがある
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
_USER_AGENT = "Mozilla/5.0 (compatible; SearchingTweetBot/1.0)"


@dataclass(frozen=True)
class FeedVideo:
    """RSS の動画 1 本分。"""

    video_id: str
    channel_id: str
    title: str
    published: datetime
    link: str

    @property
    def is_short(self) -> bool:
        return "/shorts/" in self.link


def parse_feed(xml_bytes: bytes, channel_id: str) -> List[FeedVideo]:
    """RSS の XML を FeedVideo のリストにする。"""
    root = ET.fromstring(xml_bytes)
    videos: List[FeedVideo] = []
    for e in root.findall("a:entry", _NS):
        vid = e.find("yt:videoId", _NS)
        title = e.find("a:title", _NS)
        published = e.find("a:published", _NS)
        link = e.find("a:link", _NS)
        if vid is None or published is None or not vid.text or not published.text:
            continue
        videos.append(
            FeedVideo(
                video_id=vid.text,
                channel_id=channel_id,
                title=(title.text if title is not None else "") or "",
                published=datetime.fromisoformat(published.text),
                link=(link.get("href") if link is not None else "") or "",
            )
        )
    return videos


def fetch_feed(channel_id: str, timeout: float = 20) -> List[FeedVideo]:
    """1 チャンネル分の RSS を取得する。"""
    req = urllib.request.Request(FEED_URL.format(channel_id), headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_feed(resp.read(), channel_id)


def fetch_feeds(
    channel_ids: Iterable[str],
    workers: int = 8,
    timeout: float = 20,
) -> Tuple[List[FeedVideo], Dict[str, str]]:
    """
    複数チャンネルの RSS を並列に取得する。

    Returns:
        (動画のリスト, {取得に失敗したチャンネル ID: エラー内容})
        1 チャンネルの失敗で全体を止めない。
    """
    ids = list(dict.fromkeys(channel_ids))

    def one(cid: str) -> Tuple[str, Optional[List[FeedVideo]], Optional[str]]:
        try:
            return cid, fetch_feed(cid, timeout), None
        except Exception as e:  # noqa: BLE001
            return cid, None, f"{type(e).__name__}: {e}"[:200]

    videos: List[FeedVideo] = []
    errors: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for cid, vs, err in ex.map(one, ids):
            if err is not None:
                errors[cid] = err
            else:
                videos.extend(vs or [])
    return videos, errors


def is_cover_title(title: str, cover_pattern: str, exclude_pattern: str) -> bool:
    """タイトルから歌ってみた（カバー）の動画かどうかを判定する。"""
    if not re.search(cover_pattern, title, re.IGNORECASE):
        return False
    return not re.search(exclude_pattern, title, re.IGNORECASE)
