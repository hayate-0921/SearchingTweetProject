"""
Phase 1・2 のテスト（X API は呼び出さない）。

実行方法（プロジェクトのルートで）:
    python -m unittest discover -s tests -v

フィクスチャ fixture_2026-11-07_inspection.json は、inspect_query_hits.py で取得した
直近 7 日間の実データ 37 件（ID・投稿時刻・YouTube リンクの種類・除外ワードのみ。本文なし）。
本文は判定に必要な除外ワードだけを含む合成テキストで代用する。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture_2026-11-07_inspection.json"

_TMP = tempfile.mkdtemp(prefix="retweet_test_")
_OLD_CWD = os.getcwd()


def setUpModule() -> None:
    # data/ と logs/ は相対パスのため、一時フォルダで実行して本物のデータを汚さない
    (Path(_TMP) / "data").mkdir()
    shutil.copy(ROOT / "data" / "following.json", Path(_TMP) / "data" / "following.json")
    os.chdir(_TMP)
    sys.path.insert(0, str(ROOT))
    for k in ["API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "BEARER_TOKEN"]:
        os.environ.setdefault(k, "dummy")


def tearDownModule() -> None:
    os.chdir(_OLD_CWD)
    shutil.rmtree(_TMP, ignore_errors=True)


# ----------------------------------------------------------------------
# テスト用の投稿
# ----------------------------------------------------------------------
_URL = {
    "watch": "https://www.youtube.com/watch?v={}",
    "youtu.be": "https://youtu.be/{}?si=abc",
    "shorts": "https://www.youtube.com/shorts/{}",
    "live": "https://www.youtube.com/live/{}",
}


def make_tweet(tid, created_at, refs, text="歌ってみた"):
    urls = [{"url": "https://t.co/x", "expanded_url": _URL[k].format(v)} for k, v in refs]
    return NS(id=int(tid), text=text, author_id=1, created_at=created_at, entities={"urls": urls})


def fixture_tweets():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tweets = []
    for p in data["posts"]:
        text = "歌ってみた " + " ".join(p["excluded_by"])
        created = datetime.fromisoformat(p["created_at_jst"])
        refs = [(y["kind"], y["video_id"]) for y in p["youtube"]]
        tweets.append(make_tweet(p["id"], created, refs, text))
    return tweets


T0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# youtube_url
# ----------------------------------------------------------------------
class TestYouTubeUrl(unittest.TestCase):
    def test_parse_variants(self):
        from tools.youtube_url import parse_youtube_url as p

        self.assertEqual(p("https://youtu.be/dQw4w9WgXcQ?si=x").kind, "youtu.be")
        self.assertEqual(p("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1").video_id, "dQw4w9WgXcQ")
        self.assertEqual(p("https://m.youtube.com/watch?v=dQw4w9WgXcQ").kind, "watch")
        self.assertEqual(p("https://youtube.com/shorts/dQw4w9WgXcQ").kind, "shorts")
        self.assertEqual(p("https://www.youtube.com/live/dQw4w9WgXcQ?si=1").kind, "live")
        self.assertIsNone(p("https://www.youtube.com/@channel"))
        self.assertIsNone(p("https://example.com/watch?v=dQw4w9WgXcQ"))


# ----------------------------------------------------------------------
# video_filter
# ----------------------------------------------------------------------
class TestVideoFilter(unittest.TestCase):
    def judge(self, tweet, rt_ids=(), rt_videos=()):
        from tools.video_filter import judge_tweet

        return judge_tweet(tweet, set(rt_ids), set(rt_videos), ["万再生", "short"])

    def test_reasons(self):
        self.assertEqual(self.judge(make_tweet(1, T0, [("watch", "AAAAAAAAAAA")])).reason, "accepted")
        self.assertEqual(self.judge(make_tweet(1, T0, [("watch", "AAAAAAAAAAA")]), rt_ids={"1"}).reason,
                         "already_retweeted")
        # 本編リンクがあってもショートの告知は除外
        self.assertEqual(self.judge(make_tweet(1, T0, [("shorts", "SSSSSSSSSSS"), ("watch", "AAAAAAAAAAA")])).reason,
                         "shorts")
        self.assertEqual(self.judge(make_tweet(1, T0, [("live", "LLLLLLLLLLL")])).reason, "live")
        self.assertEqual(self.judge(make_tweet(1, T0, [])).reason, "no_youtube_video")
        self.assertEqual(self.judge(make_tweet(1, T0, [("watch", "AAAAAAAAAAA")], "20万再生ありがとう")).reason,
                         "exclude_keyword")
        self.assertEqual(self.judge(make_tweet(1, T0, [("youtu.be", "AAAAAAAAAAA")]), rt_videos={"AAAAAAAAAAA"}).reason,
                         "video_already_retweeted")

    def test_first_announcement_wins(self):
        from tools.video_filter import select_candidates

        later = make_tweet(2, T0 + timedelta(hours=2), [("watch", "AAAAAAAAAAA")])
        first = make_tweet(1, T0, [("youtu.be", "AAAAAAAAAAA")])
        other = make_tweet(3, T0 + timedelta(hours=1), [("watch", "BBBBBBBBBBB")])
        accepted, rejected = select_candidates([later, other, first, first], set(), set(), [])
        self.assertEqual([j.tweet_id for j in accepted], ["1", "3"])
        self.assertEqual([(j.tweet_id, j.reason) for j in rejected], [("2", "duplicate_in_run")])

    def test_fixture_week(self):
        """実データ 37 件 → 新作 16 本だけが残る（ショート 8・配信 1・万再生 1・重複を除外）。"""
        from tools.video_filter import select_candidates

        accepted, rejected = select_candidates(fixture_tweets(), set(), set(), ["万再生", "short", "shorts"])
        reasons = {}
        for j in rejected:
            reasons[j.reason] = reasons.get(j.reason, 0) + 1
        self.assertEqual(len(accepted), 16)
        self.assertEqual(len({j.video_id for j in accepted}), 16)
        self.assertEqual(reasons, {"shorts": 8, "live": 1, "exclude_keyword": 1, "duplicate_in_run": 11})
        # 「アイドルライフスターターパック」は 4 回の告知のうち最初の 1 件だけ
        idol = [j for j in accepted if j.video_id == "6CG2Do5XS18"]
        self.assertEqual([j.tweet_id for j in idol], ["2116505628231475613"])


# ----------------------------------------------------------------------
# 検索期間
# ----------------------------------------------------------------------
class TestSearchWindow(unittest.TestCase):
    def test_window(self):
        from tools.core import get_search_window_utc as w

        now = datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc)  # JST 11/8 05:00
        end = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(w(None, now), (end - timedelta(days=1), end))
        self.assertEqual(w(end - timedelta(days=3), now), (end - timedelta(days=3), end))
        self.assertEqual(w(end - timedelta(days=10), now)[0], now - timedelta(days=7) + timedelta(minutes=5))
        self.assertIsNone(w(end, now))


# ----------------------------------------------------------------------
# Bot 全体（API は模擬）
# ----------------------------------------------------------------------
class TestBotRun(unittest.TestCase):
    def setUp(self):
        import retweeting_bot as rb

        for name in ["retweeted.json", "retweeted_videos.json", "pending.json", "state.json"]:
            path = Path("data") / name
            if path.exists():
                path.unlink()
        shutil.rmtree("data/observations", ignore_errors=True)

        self.rb = rb
        self.calls = {"search": [], "retweet": []}
        tweets = fixture_tweets()

        def fake_search_all(client, query, start_time, end_time, page_size, max_pages):
            self.calls["search"].append((start_time, end_time))
            # 最初のクエリにだけ全件を返す（それ以外は 0 件）
            data = tweets if len(self.calls["search"]) == 1 else []
            return data, {"result_count": len(data), "pages": 1, "next_token": None}

        def fake_retweet(client, tid):
            self.calls["retweet"].append(str(tid))

        self._orig = (rb.search_all_tweets, rb.retweet, rb.create_client, rb.RETWEET_LIMIT)
        rb.search_all_tweets = fake_search_all
        rb.retweet = fake_retweet
        rb.create_client = lambda suffix=None: object()
        rb.RETWEET_LIMIT = 10

    def tearDown(self):
        rb = self.rb
        rb.search_all_tweets, rb.retweet, rb.create_client, rb.RETWEET_LIMIT = self._orig

    def test_limit_and_carry_over(self):
        from tools.data_manager import load_pending, load_retweeted_videos, load_state

        # 1 回目: 22 クエリすべて検索し、採用 16 件のうち 10 件をリツイート、6 件を翌日へ
        self.rb.run_retweet_task()
        self.assertEqual(len(self.calls["search"]), 22)
        self.assertEqual(len(self.calls["retweet"]), 10)
        self.assertEqual(len(load_pending()), 6)
        self.assertEqual(len(load_retweeted_videos()), 10)
        self.assertIn("last_searched_until_utc", load_state())

        # 2 回目（同じ日）: 検索は省略し、持ち越し 6 件だけをリツイート
        self.rb.run_retweet_task()
        self.assertEqual(len(self.calls["search"]), 22)
        self.assertEqual(len(self.calls["retweet"]), 16)
        self.assertEqual(load_pending(), [])
        self.assertEqual(len(set(self.calls["retweet"])), 16)

        obs = json.loads(next(Path("data/observations").glob("*.json")).read_text(encoding="utf-8"))
        first, second = obs["runs"]
        self.assertEqual(first["summary"]["retweets"], 10)
        self.assertEqual(first["summary"]["pending_tweets"], 6)
        self.assertEqual(first["summary"]["rejected_by_reason"]["shorts"], 8)
        self.assertEqual(second["summary"]["pending_processed"], 6)
        self.assertTrue(all("text" not in t for t in first["tweets"]))

    def test_dry_run_saves_nothing(self):
        self.rb.run_retweet_task(dry_run=True)
        self.assertEqual(self.calls["retweet"], [])
        for name in ["retweeted.json", "retweeted_videos.json", "pending.json", "state.json"]:
            self.assertFalse((Path("data") / name).exists(), name)
        self.assertFalse(Path("data/observations").exists())


if __name__ == "__main__":
    unittest.main()
