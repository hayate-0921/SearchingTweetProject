"""
build_youtube_channels.py
=========================
Phase 3: にじさんじ公式サイトのタレント情報から、
X の username → YouTube チャンネル ID の対応表（data/youtube_channels.json）を作る。
あわせて、データベース（data/bot.db）の対象者マスタに表示名・公式サイトの識別子を入れる。

- 対象は following.json に入っているアカウントだけ（ユニット・公式アカウントは対応なし）
- following.json に入っていないタレントがいれば、一覧を表示する（追加漏れの確認用）
- X API は呼ばない（費用なし）。公式サイトと YouTube のページを読むだけ

新しいタレントがデビューしたときなど、following.json を更新したら実行し直す。

使い方:
    python build_youtube_channels.py            # 対応表を作って保存
    python build_youtube_channels.py --dry-run  # 保存せずに結果だけ表示
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools import database
from tools.data_manager import load_following_list, load_youtube_channels, save_youtube_channels

SITE = "https://www.nijisanji.jp"
# 対象の所属。VirtuaReal（中国）は別プロジェクトのため含めない
AFFILIATIONS = {"にじさんじ", "NIJISANJI EN"}
_UA = {"User-Agent": "Mozilla/5.0 (compatible; SearchingTweetBot/1.0)", "Accept-Language": "ja"}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def _next_data(html: str) -> Dict[str, Any]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("公式サイトの構造が変わった可能性があります（__NEXT_DATA__ なし）")
    return json.loads(m.group(1))


def _resolve_channel_id(url: str) -> Optional[str]:
    """YouTube の URL（/channel/UC... または /@handle）からチャンネル ID を求める。"""
    m = re.search(r"/channel/(UC[\w-]{22})", url)
    if m:
        return m.group(1)
    html = _get(url)
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', html) or re.search(
        r'"externalId":"(UC[\w-]{22})"', html
    )
    return m.group(1) if m else None


def fetch_talents() -> List[Dict[str, Any]]:
    """公式サイトから、対象の所属のタレント（slug・名前・X・YouTube）を取得する。"""
    top = _next_data(_get(f"{SITE}/talents"))
    build_id = top["buildId"]
    livers = top["props"]["pageProps"]["allLivers"]

    talents: List[Dict[str, Any]] = []
    for liver in livers:
        aff = set(liver.get("profile", {}).get("affiliation") or [])
        if not aff & AFFILIATIONS:
            continue
        data = _get(f"{SITE}/_next/data/{build_id}/ja/talents/l/{liver['slug']}.json")
        talents.append(
            {
                "slug": liver["slug"],
                "name": liver.get("name", ""),
                "twitter": re.findall(r'"twitter":"(https://(?:twitter|x)\.com/[^"]+)"', data),
                "youtube": re.findall(r'"youtube":"(https://www\.youtube\.com/[^"]+)"', data),
            }
        )
        time.sleep(0.3)
    return talents


def main() -> None:
    parser = argparse.ArgumentParser(description="X の username と YouTube チャンネルの対応表を作る")
    parser.add_argument("--dry-run", action="store_true", help="保存せずに結果だけ表示する")
    args = parser.parse_args()

    following = load_following_list()
    by_lower = {u.lower(): u for u in following}

    talents = fetch_talents()
    print(f"公式サイトのタレント: {len(talents)} 人")

    channels: Dict[str, str] = {}
    profiles: List[Dict[str, str]] = []
    not_following: List[str] = []
    unresolved: List[str] = []
    for t in talents:
        users = [by_lower.get(x.rstrip("/").split("/")[-1].lower()) for x in t["twitter"]]
        users = [u for u in users if u]
        if not users:
            not_following.append(f"{t['name']}（{', '.join(t['twitter']) or 'X なし'}）")
            continue
        cid = None
        for y in t["youtube"]:
            try:
                cid = _resolve_channel_id(y)
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] {t['name']}: {y} を解決できません: {e}")
            if cid:
                break
        if not cid:
            unresolved.append(t["name"])
            continue
        for u in users:
            channels[u] = cid
            profiles.append({"x_username": u, "name": t["name"], "slug": t["slug"], "youtube_channel_id": cid})

    old = load_youtube_channels()
    print(f"対応表: {len(channels)} 件（前回 {len(old)} 件）")
    for u in sorted(set(channels) - set(old), key=str.lower):
        print(f"  + {u}")
    for u in sorted(set(old) - set(channels), key=str.lower):
        print(f"  - {u}")
    if not_following:
        print(f"\nfollowing.json に入っていないタレント（{len(not_following)} 人）:")
        for x in not_following:
            print(f"  {x}")
    if unresolved:
        print(f"\nYouTube チャンネルを特定できなかったタレント: {unresolved}")

    if args.dry_run:
        print("\n（dry-run のため保存しません）")
        return
    if not channels:
        print("[ERROR] 対応表が空のため保存しません。", file=sys.stderr)
        sys.exit(1)
    save_youtube_channels(channels)
    print("\ndata/youtube_channels.json を保存しました。")

    now = datetime.now(timezone.utc)
    conn = database.connect()
    try:
        with conn:
            database.sync_targets(conn, following, channels, now)
            n = database.update_profiles(conn, profiles, now)
    finally:
        conn.close()
    print(f"data/bot.db の対象者マスタに {n} 人の表示名を入れました。")


if __name__ == "__main__":
    main()
