"""
compare_query_counts.py
=======================
Phase 0（計測）: 検索条件の案ごとに「ヒットする投稿の件数」だけを数え、
条件を変えたときに読み取り件数（＝費用）がどれだけ変わるかを見積もる。

X API の Counts: Recent（GET /2/tweets/counts/recent）を使う。
この API は投稿そのものを返さず件数だけを返すため、
件数に関係なく 1 リクエスト $0.005 で、過去 7 日分を調べられる。

  費用 = (following.json のユーザー数 ÷ 10) × 案の数 × $0.005
  例: 213 ユーザー → 22 クエリ × 4 案 = 88 リクエスト ≒ $0.44

注意: Counts API は単語 "youtube" をリンク先 URL にも当てるが、検索 API は本文にしか当てない
（Phase 0 の調査で判明）。単語で判定する条件の件数は、検索 API の結果より多く出る。

手動でのみ実行する（GitHub Actions からは実行しない）。

使い方:
    python compare_query_counts.py            # 費用見積もりだけ表示（API は呼ばない）
    python compare_query_counts.py --yes      # 実際に API を呼んで比較する
    python compare_query_counts.py --yes --variants current url
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Sequence

import tweepy

from config import (
    COST_COUNTS_REQUEST_USD,
    COST_POST_READ_USD,
    EXCLUDE_KEYWORDS,
    KEYWORDS,
    MAX_USERS_PER_SEARCH,
    load_twitter_auth,
)
from tools.core import DATA_PATH
from tools.data_manager import load_following_list
from tools.twitter_api import build_search_query

JST = timezone(timedelta(hours=9))

YOUTUBE_WORDS = '(youtu OR youtube OR "youtu.be" OR "YouTube")'
YOUTUBE_URL = '(url:"youtube.com" OR url:"youtu.be")'


def _users(usernames: Sequence[str]) -> str:
    return "(" + " OR ".join(f"from:{u}" for u in usernames) + ")"


def _keywords(words: Sequence[str]) -> str:
    return "(" + " OR ".join(words) + ")"


def _excludes(words: Sequence[str]) -> str:
    return " ".join(f"-{w}" for w in words)


# 比較する検索条件の案。キーは --variants で指定する名前。
VARIANTS: Dict[str, tuple[str, Callable[[Sequence[str]], str]]] = {
    "current": (
        "現在の条件（tools/twitter_api.build_search_query）",
        lambda users: build_search_query(users, KEYWORDS),
    ),
    "legacy_words": (
        "Phase 1 以前の条件（本文の単語で YouTube を判定。検索 API ではリンクに当たらない）",
        lambda users: " ".join([
            _users(users), YOUTUBE_WORDS, _keywords(KEYWORDS),
            "-is:retweet -is:reply", _excludes(EXCLUDE_KEYWORDS),
        ]),
    ),
    "url": (
        "YouTube を url: で判定（他は現在と同じ）",
        lambda users: " ".join([
            _users(users), YOUTUBE_URL, _keywords(KEYWORDS),
            "-is:retweet -is:reply", _excludes(EXCLUDE_KEYWORDS),
        ]),
    ),
    "url_no_exclude": (
        "url: で判定し、除外ワードなし（除外は取得後に判定する案）",
        lambda users: " ".join([
            _users(users), YOUTUBE_URL, _keywords(KEYWORDS), "-is:retweet -is:reply",
        ]),
    ),
    "url_no_keyword": (
        "url: で判定し、キーワード条件なし（YouTube のタイトルで判定する案の上限）",
        lambda users: " ".join([_users(users), YOUTUBE_URL, "-is:retweet -is:reply"]),
    ),
}


def chunks(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> None:
    parser = argparse.ArgumentParser(description="検索条件ごとのヒット件数を比較する")
    parser.add_argument("--yes", action="store_true", help="実際に API を呼ぶ（課金あり）")
    parser.add_argument(
        "--variants", nargs="+", choices=list(VARIANTS), default=["current", "url_no_exclude", "url_no_keyword"],
        help="比較する案（既定: すべて）",
    )
    args = parser.parse_args()

    usernames = load_following_list()
    groups = chunks(usernames, MAX_USERS_PER_SEARCH)
    n_requests = len(groups) * len(args.variants)
    estimate = n_requests * COST_COUNTS_REQUEST_USD

    print(f"対象ユーザー: {len(usernames)} 人 / クエリ {len(groups)} 本 × 案 {len(args.variants)} 個")
    print(f"リクエスト数: {n_requests} 回 → 概算 ${estimate:.3f}")
    for name in args.variants:
        sample = VARIANTS[name][1](groups[0])
        print(f"  [{name}] {VARIANTS[name][0]}（クエリ長 最大 "
              f"{max(len(VARIANTS[name][1](g)) for g in groups)} 文字）")
        print(f"     例: {sample[:160]}{'…' if len(sample) > 160 else ''}")

    if not args.yes:
        print("\n見積もりのみ表示しました。実行するには --yes を付けてください。")
        return

    auth = load_twitter_auth()
    client = tweepy.Client(bearer_token=auth.bearer_token, wait_on_rate_limit=True)

    # 直近 7 日間（Counts: Recent の上限）。end_time は現在の少し前にする必要がある。
    end_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    start_time = end_time - timedelta(days=7) + timedelta(minutes=2)

    results: Dict[str, Dict[str, object]] = {}
    for name in args.variants:
        build = VARIANTS[name][1]
        total = 0
        per_group: List[int] = []
        errors: List[str] = []
        for g in groups:
            try:
                resp = client.get_recent_tweets_count(
                    build(g), granularity="day", start_time=start_time, end_time=end_time,
                )
                n = int((getattr(resp, "meta", None) or {}).get("total_tweet_count", 0))
            except tweepy.TweepyException as e:
                n = 0
                errors.append(f"{g[0]}…: {e}")
            per_group.append(n)
            total += n
        results[name] = {
            "label": VARIANTS[name][0],
            "total_7days": total,
            "per_day": round(total / 7, 1),
            "estimated_read_usd_per_day": round(total / 7 * COST_POST_READ_USD, 4),
            "max_per_query_7days": max(per_group) if per_group else 0,
            "errors": errors,
        }

    print("\n" + "=" * 60)
    print("直近 7 日間のヒット件数（投稿は読み取っていません）")
    print("=" * 60)
    base = results.get("current", {}).get("total_7days")
    for name, r in results.items():
        diff = ""
        if base and name != "current":
            diff = f"（現在比 {r['total_7days'] / base * 100:.0f}%）"
        print(f"[{name}] {r['label']}")
        print(f"   7 日間 {r['total_7days']} 件 / 1 日平均 {r['per_day']} 件{diff}")
        print(f"   全件を読んだ場合の読み取り費用: 1 日 ${r['estimated_read_usd_per_day']}")
        if r["errors"]:
            print(f"   エラー {len(r['errors'])} 件: {r['errors'][:2]}")

    out_dir = DATA_PATH / "query_counts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now(JST).strftime('%Y-%m-%d_%H%M')}.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "window_utc": [start_time.isoformat(), end_time.isoformat()],
                "users": len(usernames),
                "requests": n_requests,
                "estimated_cost_usd": round(estimate, 4),
                "results": results,
            },
            f, ensure_ascii=False, indent=2,
        )
    print(f"\n結果を保存しました: {out}")


if __name__ == "__main__":
    main()
