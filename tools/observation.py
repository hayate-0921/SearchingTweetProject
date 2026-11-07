"""
observation.py
---------------
Phase 0（計測）用の観測記録モジュール。

本番の実行中に「何を検索し、何件読み、何をリツイートし、何を見送ったか」を
data/observations/YYYY-MM-DD.json（JST の実行日）に記録する。
API を追加で呼び出すことはないため、記録による費用は発生しない。

公開リポジトリに保存されるため、X の規約に配慮してツイート本文は保存しない。
代わりに、ツイート ID（URL で開ける）と、本文から機械的に求めた
判定材料（一致したキーワード、YouTube 動画 ID など）だけを保存する。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from config import (
    COST_POST_READ_USD,
    COST_RETWEET_USD,
    EXCLUDE_KEYWORDS,
    KEYWORD_CANDIDATES,
)
from .core import DATA_PATH
from .youtube_url import extract_video_refs, is_youtube_url

OBSERVATION_DIR = DATA_PATH / "observations"
JST = timezone(timedelta(hours=9))


def _matched(text: str, words: Sequence[str]) -> List[str]:
    """text に含まれる語を（大文字小文字を区別せず）返す。"""
    lowered = text.lower()
    return [w for w in words if w.lower() in lowered]


def _expanded_urls(tweet: Any) -> List[str]:
    """Tweet の entities.urls から展開済み URL を取り出す。"""
    entities = getattr(tweet, "entities", None) or {}
    urls = []
    for u in entities.get("urls", []) or []:
        expanded = u.get("unwound_url") or u.get("expanded_url") or u.get("url")
        if expanded:
            urls.append(expanded)
    return urls


class RunRecorder:
    """
    1 回の実行分の観測記録を保持し、最後にファイルへ保存するクラス。

    使い方:
        recorder = RunRecorder(account_label, start_time, end_time, keywords)
        idx = recorder.add_search(query, users, meta, tweets)
        recorder.set_decision(tweet_id, "retweeted")
        recorder.finish(status="completed")
    """

    def __init__(
        self,
        account_label: str,
        start_time: datetime,
        end_time: datetime,
        keywords: Sequence[str],
        total_queries: int = 0,
    ) -> None:
        self.started_at = datetime.now(JST)
        self.run: Dict[str, Any] = {
            "account": account_label,
            "started_at": self.started_at.isoformat(),
            "search_window": {
                "start_utc": start_time.isoformat(),
                "end_utc": end_time.isoformat(),
            },
            "keywords": list(keywords),
            "exclude_keywords": list(EXCLUDE_KEYWORDS),
            "total_queries": total_queries,
            "searches": [],
            "tweets": [],
        }
        self._tweet_index: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------
    def set_total_queries(self, n: int) -> None:
        self.run["total_queries"] = n

    def add_search(
        self,
        query: str,
        usernames: Sequence[str],
        meta: Optional[dict],
        tweets: Sequence[Any],
    ) -> int:
        """
        検索 1 回分の結果を記録する。

        meta に next_token がある場合は「取得件数の上限で打ち切られ、
        続きを読んでいない」ことを意味する（取りこぼしの証拠）。
        """
        meta = dict(meta or {})
        index = len(self.run["searches"])
        self.run["searches"].append(
            {
                "index": index,
                "usernames": list(usernames),
                "query_length": len(query),
                "result_count": meta.get("result_count", len(tweets)),
                "truncated": bool(meta.get("next_token")),
            }
        )

        for tw in tweets:
            tid = str(tw.id)
            if tid in self._tweet_index:
                continue
            text = getattr(tw, "text", "") or ""
            urls = _expanded_urls(tw)
            created_at = getattr(tw, "created_at", None)
            record = {
                "id": tid,
                "url": f"https://x.com/i/status/{tid}",
                "author_id": str(getattr(tw, "author_id", "") or ""),
                "created_at": created_at.isoformat() if created_at else None,
                "search_index": index,
                "has_youtube_url": any(is_youtube_url(u) for u in urls),
                "youtube": [
                    {"video_id": r.video_id, "kind": r.kind}
                    for r in extract_video_refs(urls)
                ],
                "other_url_hosts": sorted(
                    {u.split("/")[2] for u in urls if "://" in u and not is_youtube_url(u)}
                ),
                "matched_keywords": _matched(text, KEYWORD_CANDIDATES),
                "matched_exclude": _matched(text, EXCLUDE_KEYWORDS),
                "mentions_short": "short" in text.lower(),
                "decision": "not_processed",
            }
            self._tweet_index[tid] = record
            self.run["tweets"].append(record)
        return index

    def set_decision(self, tweet_id: str, decision: str, detail: str | None = None) -> None:
        """
        ツイートの処理結果を記録する。

        decision の値:
          - "retweeted"        : リツイートした
          - "already_retweeted": 過去にリツイート済みのため見送り
          - "limit_reached"    : 1 日の上限に達したため見送り（翌日にも回らない）
          - "failed"           : リツイートに失敗
          - "not_processed"    : 上限到達で処理ループが終了し、判定されなかった
        """
        record = self._tweet_index.get(str(tweet_id))
        if record is None:
            return
        record["decision"] = decision
        if detail:
            record["detail"] = detail

    # ------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        searches = self.run["searches"]
        tweets = self.run["tweets"]
        posts_read = sum(s["result_count"] or 0 for s in searches)
        retweets = sum(1 for t in tweets if t["decision"] == "retweeted")
        return {
            "search_requests": len(searches),
            "queries_not_executed": max(self.run["total_queries"] - len(searches), 0),
            "posts_read": posts_read,
            "truncated_searches": sum(1 for s in searches if s["truncated"]),
            "retweets": retweets,
            "limit_reached_tweets": sum(
                1 for t in tweets if t["decision"] in ("limit_reached", "not_processed")
            ),
            "estimated_cost_usd": {
                "post_read": round(posts_read * COST_POST_READ_USD, 4),
                "retweet": round(retweets * COST_RETWEET_USD, 4),
                "total": round(
                    posts_read * COST_POST_READ_USD + retweets * COST_RETWEET_USD, 4
                ),
            },
        }

    def finish(self, status: str) -> Path:
        """
        記録をファイルへ保存し、保存先のパスを返す。

        同じ日に複数回実行した場合（手動実行など）は runs 配列に追記する。
        """
        self.run["status"] = status
        self.run["finished_at"] = datetime.now(JST).isoformat()
        self.run["summary"] = self.summary()

        OBSERVATION_DIR.mkdir(parents=True, exist_ok=True)
        path = OBSERVATION_DIR / f"{self.started_at.date().isoformat()}.json"

        data: Dict[str, Any] = {"runs": []}
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = {"runs": []}
        data.setdefault("runs", []).append(self.run)

        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path
