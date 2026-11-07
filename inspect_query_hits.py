"""
inspect_query_hits.py
=====================
Phase 0（計測）: 検索条件に当たる直近 7 日間の投稿を実際に取得し、
retweeted.json と照らし合わせて「どれを取りこぼしたか」「なぜか」を調べる。

手順:
  1. 除外ワードなしの条件（url_no_exclude）で直近 7 日間の投稿を全件取得する
  2. 現在の条件（current）でも取得し、どれが現在の条件に当たるかを確定する
     （同じ投稿を UTC の同じ日に再取得しても課金されないため、追加費用はほぼない）
  3. Bot と同じ呼び出し方（日本時間の前日分・最大 10 件）を日ごとに再現し、
     Bot の検索で本当に返ってくるかを確かめる（--no-replay で省略可）
  4. 投稿ごとに、リツイート済みか・どの日の実行で拾われるはずだったかを表示する

費用の目安: 取得件数 × $0.005（compare_query_counts.py の結果では約 37 件 ≒ $0.19）。
結果が 0 件の検索リクエストは課金されない。

本文は画面に表示するだけで、ファイルには保存しない（公開リポジトリのため）。

使い方:
    python inspect_query_hits.py             # 見積もりのみ（API は呼ばない）
    python inspect_query_hits.py --yes       # 実行
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import tweepy

from compare_query_counts import VARIANTS, chunks
from config import COST_POST_READ_USD, EXCLUDE_KEYWORDS, MAX_TWEETS_PER_SEARCH, load_twitter_auth
from tools.core import DATA_PATH
from tools.data_manager import load_following_list, load_retweeted_list
from tools.youtube_url import extract_video_refs

JST = timezone(timedelta(hours=9))
TWEET_FIELDS = ["id", "text", "author_id", "created_at", "entities"]


def fetch_all(client: tweepy.Client, query: str, start: datetime, end: datetime) -> List[Any]:
    """next_token をたどって、条件に当たる投稿をすべて取得する。"""
    tweets: List[Any] = []
    for page in tweepy.Paginator(
        client.search_recent_tweets,
        query=query,
        start_time=start,
        end_time=end,
        max_results=100,
        tweet_fields=TWEET_FIELDS,
    ):
        tweets.extend(page.data or [])
    return tweets


def expanded_urls(tw: Any) -> List[str]:
    entities = getattr(tw, "entities", None) or {}
    return [
        u.get("unwound_url") or u.get("expanded_url") or u.get("url")
        for u in entities.get("urls", []) or []
    ]


def bot_run_date(created_at: datetime) -> str:
    """その投稿を拾うはずだった Bot の実行日（JST）。投稿日（JST）の翌日。"""
    return (created_at.astimezone(JST).date() + timedelta(days=1)).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="検索条件に当たる投稿を取得して照合する")
    parser.add_argument("--yes", action="store_true", help="実際に API を呼ぶ（課金あり）")
    parser.add_argument("--no-replay", action="store_true", help="Bot の呼び出し方の再現を省略する")
    args = parser.parse_args()

    usernames = load_following_list()
    groups = chunks(usernames, 10)

    print(f"対象ユーザー: {len(usernames)} 人 / クエリ {len(groups)} 本")
    print("取得件数 × $0.005 の費用がかかります（前回の件数比較では約 37 件 ≒ $0.19）。")
    if not args.yes:
        print("見積もりのみ表示しました。実行するには --yes を付けてください。")
        return

    auth = load_twitter_auth()
    client = tweepy.Client(bearer_token=auth.bearer_token, wait_on_rate_limit=True)
    retweeted = load_retweeted_list()

    now = datetime.now(timezone.utc)
    end = now - timedelta(minutes=1)
    start = now - timedelta(days=7) + timedelta(minutes=2)

    # ---- 1) 除外ワードなしで全件取得、2) 現在の条件で取得 ----
    posts: Dict[str, Dict[str, Any]] = {}
    current_ids: set[str] = set()
    for gi, g in enumerate(groups):
        for tw in fetch_all(client, VARIANTS["url_no_exclude"][1](g), start, end):
            posts.setdefault(str(tw.id), {"tweet": tw, "group": gi})
        for tw in fetch_all(client, VARIANTS["current"][1](g), start, end):
            posts.setdefault(str(tw.id), {"tweet": tw, "group": gi})
            current_ids.add(str(tw.id))

    # ---- 3) Bot と同じ呼び出し方を日ごとに再現 ----
    replay_found: Dict[str, str] = {}
    replay_days: List[str] = []
    if not args.no_replay:
        today_jst = now.astimezone(JST).date()
        for back in range(1, 8):
            day = today_jst - timedelta(days=back)  # 検索対象の日（JST）
            day_start = datetime(day.year, day.month, day.day, tzinfo=JST).astimezone(timezone.utc)
            day_end = day_start + timedelta(days=1)
            if day_start < start:
                continue  # 7 日より前は検索 API で遡れない
            replay_days.append(day.isoformat())
            for g in groups:
                resp = client.search_recent_tweets(
                    query=VARIANTS["current"][1](g),
                    start_time=day_start,
                    end_time=day_end,
                    max_results=MAX_TWEETS_PER_SEARCH,
                    tweet_fields=TWEET_FIELDS,
                )
                for tw in resp.data or []:
                    replay_found[str(tw.id)] = day.isoformat()

    # ---- 4) 照合と表示 ----
    rows = []
    for tid, p in sorted(posts.items(), key=lambda kv: kv[1]["tweet"].created_at):
        tw = p["tweet"]
        text = (tw.text or "").replace("\n", " ")
        refs = extract_video_refs(expanded_urls(tw))
        rows.append({
            "id": tid,
            "url": f"https://x.com/i/status/{tid}",
            "created_at_jst": tw.created_at.astimezone(JST).isoformat(timespec="minutes"),
            "expected_bot_run_jst": bot_run_date(tw.created_at),
            "query_group": p["group"],
            "in_current_query": tid in current_ids,
            "excluded_by": [w for w in EXCLUDE_KEYWORDS if w.lower() in text.lower()],
            "youtube": [{"video_id": r.video_id, "kind": r.kind} for r in refs],
            "retweeted": tid in retweeted,
            "replay_found": replay_found.get(tid),
            "_text": text,
        })

    print("\n" + "=" * 70)
    print(f"取得した投稿: {len(rows)} 件（うち現在の条件に当たる {len(current_ids)} 件）")
    print("=" * 70)
    for r in rows:
        mark = "RT済" if r["retweeted"] else ("未RT" if r["in_current_query"] else "条件外")
        yt = ",".join(f"{y['kind']}:{y['video_id']}" for y in r["youtube"]) or "YouTubeなし"
        replay = r["replay_found"] or ("再現対象外" if args.no_replay else "再現で返らず")
        print(f"[{mark}] {r['created_at_jst']}  {r['url']}")
        print(f"      Bot実行予定日 {r['expected_bot_run_jst']} / 再現: {replay} / {yt}"
              + (f" / 除外語: {r['excluded_by']}" if r["excluded_by"] else ""))
        print(f"      {r['_text'][:90]}")

    today = now.astimezone(JST).date().isoformat()
    in_cur = [r for r in rows if r["in_current_query"]]
    pending = [r for r in in_cur if not r["retweeted"] and r["expected_bot_run_jst"] > today]
    missed = [r for r in in_cur if not r["retweeted"] and r["expected_bot_run_jst"] <= today]
    print("\n■ 集計")
    print(f"  現在の条件に当たる投稿      : {len(in_cur)} 件")
    print(f"    リツイート済み            : {len(in_cur) - len(missed) - len(pending)} 件")
    print(f"    Bot の実行前（今日の投稿）: {len(pending)} 件")
    print(f"    取りこぼし                : {len(missed)} 件")
    if not args.no_replay:
        print(f"      うち Bot の呼び出し方で返る   : {sum(1 for r in missed if r['replay_found'])} 件")
        print(f"      うち Bot の呼び出し方で返らない: {sum(1 for r in missed if not r['replay_found'])} 件")
        print(f"      （再現した日: {', '.join(sorted(replay_days))}）")
    print(f"  現在の条件では返らない投稿  : {len(rows) - len(in_cur)} 件"
          f"（うち除外ワードを含む {sum(1 for r in rows if not r['in_current_query'] and r['excluded_by'])} 件）")
    print(f"  概算費用                    : 約 ${len(rows) * COST_POST_READ_USD:.3f}"
          "（同日の再取得は課金されない前提）")

    out_dir = DATA_PATH / "query_inspections"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{now.astimezone(JST).strftime('%Y-%m-%d_%H%M')}.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "window_utc": [start.isoformat(), end.isoformat()],
                "replay_days_jst": sorted(replay_days),
                "posts": [{k: v for k, v in r.items() if k != "_text"} for r in rows],
            },
            f, ensure_ascii=False, indent=2,
        )
    print(f"\n結果を保存しました（本文は含みません）: {out}")


if __name__ == "__main__":
    main()
