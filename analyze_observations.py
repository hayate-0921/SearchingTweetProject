"""
analyze_observations.py
=======================
Phase 0（計測）: data/observations/*.json に蓄積した観測記録を集計し、
取りこぼし・取りすぎ・費用の判断材料をレポートとして表示する。

このスクリプトは X API を一切呼び出さない（費用 0）。

使い方:
    python analyze_observations.py              # 全期間
    python analyze_observations.py --days 7     # 直近 7 日分
    python analyze_observations.py --json       # 機械可読な JSON で出力
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

from config import COST_POST_READ_USD, COST_RETWEET_USD, EXCLUDE_KEYWORDS
from tools.observation import OBSERVATION_DIR


def load_runs(days: int | None) -> List[Dict[str, Any]]:
    files = sorted(OBSERVATION_DIR.glob("*.json"))
    if days:
        files = files[-days:]
    runs: List[Dict[str, Any]] = []
    for path in files:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for run in data.get("runs", []):
            run["_file"] = path.name
            runs.append(run)
    return runs


def analyze(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    n_runs = len(runs)
    status = Counter(r.get("status") for r in runs)

    searches = [s for r in runs for s in r.get("searches", [])]
    tweets = [t for r in runs for t in r.get("tweets", [])]

    posts_read = sum(s.get("result_count") or 0 for s in searches)
    retweeted = [t for t in tweets if t.get("decision") == "retweeted"]

    # --- 取りこぼしの兆候 ---
    truncated = [s for s in searches if s.get("truncated")]
    not_executed = sum(r.get("summary", {}).get("queries_not_executed", 0) for r in runs)
    limit_dropped = [t for t in tweets if t.get("decision") in ("limit_reached", "not_processed")]

    # --- 取りすぎの兆候 ---
    no_youtube = [t for t in tweets if not t.get("has_youtube_url")]
    shorts_url = [t for t in tweets if any(y["kind"] == "shorts" for y in t.get("youtube", []))]
    youtu_be = [t for t in tweets if any(y["kind"] == "youtu.be" for y in t.get("youtube", []))]
    live_url = [t for t in tweets if any(y["kind"] == "live" for y in t.get("youtube", []))]

    video_to_tweets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in tweets:
        for y in t.get("youtube", []):
            video_to_tweets[y["video_id"]].append(t)
    dup_videos = {v: ts for v, ts in video_to_tweets.items() if len(ts) > 1}
    dup_retweets = {
        v: [t for t in ts if t.get("decision") == "retweeted"]
        for v, ts in video_to_tweets.items()
    }
    dup_retweets = {v: ts for v, ts in dup_retweets.items() if len(ts) > 1}

    keyword_hits = Counter(k for t in tweets for k in t.get("matched_keywords", []))
    no_keyword = [t for t in tweets if not t.get("matched_keywords")]

    retweet_cost = len(retweeted) * COST_RETWEET_USD
    read_cost = posts_read * COST_POST_READ_USD

    return {
        "period": sorted({r["_file"][:-5] for r in runs}),
        "runs": n_runs,
        "run_status": dict(status),
        "cost": {
            "search_requests": len(searches),
            "posts_read": posts_read,
            "retweets": len(retweeted),
            "read_usd": round(read_cost, 4),
            "retweet_usd": round(retweet_cost, 4),
            "total_usd": round(read_cost + retweet_cost, 4),
            "per_run_usd": round((read_cost + retweet_cost) / n_runs, 4) if n_runs else 0,
            "retweet_share": round(retweet_cost / (read_cost + retweet_cost), 3)
            if (read_cost + retweet_cost) else 0,
        },
        "omission_signals": {
            "truncated_searches": len(truncated),
            "truncated_search_users": sorted({u for s in truncated for u in s.get("usernames", [])}),
            "queries_not_executed_due_to_limit": not_executed,
            "tweets_dropped_by_limit": len(limit_dropped),
            "tweets_dropped_by_limit_ids": [t["url"] for t in limit_dropped],
        },
        "overfetch_signals": {
            "tweets_total": len(tweets),
            "unique_videos": len(video_to_tweets),
            "tweets_without_youtube_url": len(no_youtube),
            "tweets_with_shorts_url": len(shorts_url),
            "shorts_retweeted": [t["url"] for t in shorts_url if t.get("decision") == "retweeted"],
            "tweets_with_youtu_be_url": len(youtu_be),
            "tweets_with_live_url": len(live_url),
            "videos_announced_multiple_times": len(dup_videos),
            "videos_retweeted_multiple_times": {
                v: [t["url"] for t in ts] for v, ts in dup_retweets.items()
            },
        },
        "keywords": {
            "hits": dict(keyword_hits.most_common()),
            "tweets_without_any_candidate_keyword": len(no_keyword),
            "exclude_keywords_in_query": list(EXCLUDE_KEYWORDS),
        },
    }


def print_report(rep: Dict[str, Any]) -> None:
    c, o, f, k = rep["cost"], rep["omission_signals"], rep["overfetch_signals"], rep["keywords"]
    period = rep["period"]
    print("=" * 60)
    print("観測レポート（Phase 0）")
    print("=" * 60)
    if not period:
        print("観測記録がまだありません（data/observations/ が空です）。")
        return
    print(f"期間: {period[0]} 〜 {period[-1]}（{len(period)} 日 / 実行 {rep['runs']} 回）")
    print(f"実行結果: {rep['run_status']}")

    print("\n■ 費用（概算）")
    print(f"  検索リクエスト    : {c['search_requests']} 回")
    print(f"  読み取った投稿    : {c['posts_read']} 件  → ${c['read_usd']}")
    print(f"  リツイート        : {c['retweets']} 件  → ${c['retweet_usd']}")
    print(f"  合計              : ${c['total_usd']}（1 回あたり ${c['per_run_usd']}）")
    print(f"  うちリツイート分  : {c['retweet_share'] * 100:.1f}%")

    print("\n■ 取りこぼしの兆候")
    print(f"  上限 10 件で打ち切られた検索 : {o['truncated_searches']} 回")
    if o["truncated_search_users"]:
        print(f"    対象ユーザー例: {o['truncated_search_users'][:10]}")
    print(f"  リツイート上限で未実行の検索 : {o['queries_not_executed_due_to_limit']} 回")
    print(f"  リツイート上限で見送った投稿 : {o['tweets_dropped_by_limit']} 件（翌日にも回らない）")

    print("\n■ 取りすぎの兆候")
    print(f"  取得した投稿          : {f['tweets_total']} 件（動画の種類 {f['unique_videos']} 本）")
    print(f"  YouTube リンクなし    : {f['tweets_without_youtube_url']} 件")
    print(f"  shorts 形式のリンク   : {f['tweets_with_shorts_url']} 件"
          f"（うちリツイート {len(f['shorts_retweeted'])} 件）")
    print(f"  youtu.be 形式のリンク : {f['tweets_with_youtu_be_url']} 件（ショートか URL では判別不可）")
    print(f"  live 形式のリンク     : {f['tweets_with_live_url']} 件（配信・プレミア）")
    print(f"  複数回告知された動画  : {f['videos_announced_multiple_times']} 本")
    print(f"  複数回リツイートした動画: {len(f['videos_retweeted_multiple_times'])} 本")

    print("\n■ 本文に含まれていた語（取得した投稿のうち）")
    for word, n in k["hits"].items():
        print(f"  {word:<16} {n} 件")
    print(f"  候補語を 1 つも含まない投稿: {k['tweets_without_any_candidate_keyword']} 件")


def main() -> None:
    parser = argparse.ArgumentParser(description="観測記録を集計する（API は使わない）")
    parser.add_argument("--days", type=int, default=None, help="直近 N 日分だけ集計する")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    args = parser.parse_args()

    rep = analyze(load_runs(args.days))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep)


if __name__ == "__main__":
    main()
