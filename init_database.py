"""
init_database.py
================
データベース（data/bot.db）を作り、これまでの JSON の記録を取り込む（最初に 1 回だけ実行する）。

取り込むもの:
  - 対象者マスタ   : following.json・youtube_channels.json
  - 実行記録       : data/observations/*.json の各実行
  - 告知ツイート   : 観測記録でリツイートしたもの（見つけた方法・ユーザー ID つき）
                     ＋ retweeted_videos.json にだけあるもの（観測記録より前の分）

表示名を入れるには、このあと build_youtube_channels.py を実行する。
X API・YouTube は呼ばない（費用なし）。

使い方:
    python init_database.py          # data/bot.db がなければ作って取り込む
    python init_database.py --force  # 作り直す（既存の data/bot.db を消す）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from tools import database
from tools.core import DATA_PATH
from tools.data_manager import load_following_list, load_retweeted_videos, load_youtube_channels

OBSERVATION_DIR = DATA_PATH / "observations"


def _retweets_from_run(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """観測記録 1 回分から、リツイートした告知を取り出す。"""
    finished = run.get("finished_at") or run.get("started_at")
    retweeted_at = datetime.fromisoformat(finished) if finished else None
    sources = {s["index"]: s.get("source", "keyword") for s in run.get("searches", [])}

    items: List[Dict[str, Any]] = []
    for t in run.get("tweets", []):
        if t.get("decision") != "retweeted":
            continue
        video = t.get("judged_video_id") or next((y["video_id"] for y in t.get("youtube", [])), None)
        if not video:
            continue
        source = sources.get(t.get("search_index"), "keyword")
        items.append(
            {
                "tweet_id": t["id"],
                "video_id": video,
                "author_id": t.get("author_id") or None,
                "found_by": source.split(":", 1)[-1],
                "retweeted_at": retweeted_at,
            }
        )
    for p in run.get("pending_processed", []):
        if p.get("decision") == "retweeted" and p.get("video_ids"):
            items.append(
                {"tweet_id": p["id"], "video_id": p["video_ids"][0], "author_id": None,
                 "found_by": None, "retweeted_at": retweeted_at}
            )
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="data/bot.db を作り、これまでの記録を取り込む")
    parser.add_argument("--force", action="store_true", help="既存の data/bot.db を消して作り直す")
    args = parser.parse_args()

    if database.DB_FILE.exists():
        if not args.force:
            print(f"{database.DB_FILE} は既にあります。作り直す場合は --force を付けてください。", file=sys.stderr)
            sys.exit(1)
        database.DB_FILE.unlink()

    now = datetime.now(timezone.utc)
    conn = database.connect()
    try:
        with conn:
            database.sync_targets(conn, load_following_list(), load_youtube_channels(), now)

            runs = 0
            tweets = 0
            for path in sorted(OBSERVATION_DIR.glob("*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                for run in data.get("runs", []):
                    if "status" not in run:
                        continue
                    run_id = database.record_run(conn, run, run.get("summary") or {})
                    tweets += database.record_retweets(conn, _retweets_from_run(run), run_id, now)
                    runs += 1

            # 観測記録より前にリツイートした分（ツイート ID と動画 ID だけ分かる）
            older = [
                {"tweet_id": tid, "video_id": vid, "author_id": None, "found_by": None, "retweeted_at": None}
                for vid, tid in load_retweeted_videos().items()
            ]
            before = conn.execute("SELECT COUNT(*) FROM announce_tweets").fetchone()[0]
            database.record_retweets(conn, older, None, now)
            older_added = conn.execute("SELECT COUNT(*) FROM announce_tweets").fetchone()[0] - before

        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ["targets", "cover_videos", "announce_tweets", "runs"]
        }
    finally:
        conn.close()

    print(f"{database.DB_FILE} を作りました。")
    print(f"  実行記録       : {runs} 回")
    print(f"  告知ツイート   : 観測記録から {tweets} 件 ＋ それより前の分 {older_added} 件")
    print(f"  件数           : {counts}")
    print("\n表示名を入れるには python build_youtube_channels.py を実行してください。")


if __name__ == "__main__":
    main()
