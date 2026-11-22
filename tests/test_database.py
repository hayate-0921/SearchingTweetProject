"""
データベース（data/bot.db）への記録のテスト。

実行方法（プロジェクトのルートで）:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import database as db  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 22, 15, 0, tzinfo=UTC)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db_test_")
        self.path = Path(self.tmp) / "bot.db"
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, *args):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def test_tweet_time_from_id(self):
        # 2026-11-20T12:05:24Z の投稿（観測記録の実例）
        self.assertTrue(db.tweet_time("2123749547935760399").startswith("2026-11-20T12:05:24"))
        self.assertIsNone(db.tweet_time("abc"))

    def test_sync_targets_keeps_removed_users_inactive(self):
        db.sync_targets(self.conn, ["Kanae_2434", "unit_x"], {"Kanae_2434": "UC_KANAE"}, NOW)
        db.sync_targets(self.conn, ["Kanae_2434"], {"Kanae_2434": "UC_KANAE"}, NOW)
        got = {r["x_username"]: r for r in self.rows("SELECT * FROM targets")}
        self.assertEqual(got["Kanae_2434"]["kind"], "talent")
        self.assertEqual(got["Kanae_2434"]["active"], 1)
        self.assertEqual(got["unit_x"]["kind"], "other")
        self.assertEqual(got["unit_x"]["active"], 0)  # 消さずに残す

    def test_profiles_survive_sync(self):
        db.update_profiles(
            self.conn,
            [{"x_username": "Kanae_2434", "name": "叶", "slug": "kanae", "youtube_channel_id": "UC_KANAE"}],
            NOW,
        )
        db.sync_targets(self.conn, ["Kanae_2434"], {"Kanae_2434": "UC_KANAE"}, NOW)
        row = self.rows("SELECT name, official_slug FROM targets")[0]
        self.assertEqual(row, {"name": "叶", "official_slug": "kanae"})

    def test_cover_video_then_retweet(self):
        db.sync_targets(self.conn, ["Hibachi_Mana"], {"Hibachi_Mana": "UC_MANA"}, NOW)
        video = {
            "video_id": "Ss_Jj35EG18",
            "x_username": "Hibachi_Mana",
            "channel_id": "UC_MANA",
            "title": "【歌ってみた】花が落ちたので、",
            "published_at": NOW - timedelta(days=1),
        }
        self.assertEqual(db.record_cover_videos(self.conn, [video], NOW), 1)
        self.assertEqual(db.record_cover_videos(self.conn, [video], NOW), 0)  # 2 回目は増えない

        n = db.record_retweets(
            self.conn,
            [{"tweet_id": "2123749547935760399", "video_id": "Ss_Jj35EG18", "author_id": "111", "found_by": "youtube_url"}],
            run_id=1,
            now=NOW,
        )
        self.assertEqual(n, 1)
        tw = self.rows("SELECT * FROM announce_tweets")[0]
        self.assertEqual(tw["x_username"], "Hibachi_Mana")  # 動画（②）から分かる
        self.assertEqual(tw["found_by"], "youtube_url")
        self.assertTrue(tw["tweet_created_at"].startswith("2026-11-20"))
        # ユーザー ID を覚える
        self.assertEqual(self.rows("SELECT x_user_id FROM targets")[0]["x_user_id"], "111")

    def test_learning_user_id_backfills_old_records(self):
        db.sync_targets(self.conn, ["Kanae_2434"], {"Kanae_2434": "UC_KANAE"}, NOW)
        # 過去分: 動画もアカウント名も分からない
        db.record_retweets(
            self.conn,
            [{"tweet_id": "1", "video_id": "OLDVIDEO001", "author_id": "333", "found_by": None, "retweeted_at": None}],
            run_id=None,
            now=NOW,
        )
        self.assertIsNone(self.rows("SELECT x_username FROM announce_tweets")[0]["x_username"])
        # 新しい動画は RSS で対象者が分かる → ユーザー ID を覚え、過去分も埋まる
        db.record_cover_videos(
            self.conn,
            [{"video_id": "NEWVIDEO001", "x_username": "Kanae_2434", "channel_id": "UC_KANAE", "title": "cover",
              "published_at": NOW}],
            NOW,
        )
        db.record_retweets(
            self.conn,
            [{"tweet_id": "2", "video_id": "NEWVIDEO001", "author_id": "333", "found_by": "keyword"}],
            run_id=None,
            now=NOW,
        )
        users = dict(self.conn.execute("SELECT tweet_id, x_username FROM announce_tweets").fetchall())
        self.assertEqual(users, {"1": "Kanae_2434", "2": "Kanae_2434"})
        old = self.rows("SELECT c.x_username, a.retweeted_at FROM cover_videos c JOIN announce_tweets a USING (video_id) "
                        "WHERE tweet_id = '1'")[0]
        self.assertEqual(old, {"x_username": "Kanae_2434", "retweeted_at": None})

    def test_retweet_of_unknown_video_uses_learned_user_id(self):
        db.sync_targets(self.conn, ["Kanae_2434"], {"Kanae_2434": "UC_KANAE"}, NOW)
        self.conn.execute("UPDATE targets SET x_user_id = '222'")
        db.record_retweets(
            self.conn,
            [{"tweet_id": "2123749547935760400", "video_id": "NEWVIDEO001", "author_id": "222", "found_by": "keyword"}],
            run_id=None,
            now=NOW,
        )
        video = self.rows("SELECT * FROM cover_videos")[0]
        self.assertEqual(video["detected_by"], "x")  # RSS になかった動画は X 経由として加える
        self.assertEqual(video["x_username"], "Kanae_2434")
        self.assertEqual(self.rows("SELECT x_username FROM announce_tweets")[0]["x_username"], "Kanae_2434")

    def test_record_execution(self):
        run = {
            "account": "DEFAULT",
            "started_at": "2026-11-23T00:01:00+09:00",
            "finished_at": "2026-11-23T00:03:00+09:00",
            "status": "completed",
            "search_window": {"start_utc": "2026-11-21T15:00:00+00:00", "end_utc": "2026-11-22T15:00:00+00:00"},
        }
        summary = {"search_requests": 22, "posts_read": 9, "retweets": 1, "estimated_cost_usd": {"total": 0.06}}
        result = db.record_execution(
            run, summary, ["Kanae_2434"], {"Kanae_2434": "UC_KANAE"}, [],
            [{"tweet_id": "2123749547935760399", "video_id": "tjROC5CQmAo", "author_id": "1", "found_by": "keyword"}],
            now=NOW, path=self.path,
        )
        self.assertEqual(result["new_announce_tweets"], 1)
        r = self.rows("SELECT * FROM runs")[0]
        self.assertEqual(r["started_at"], "2026-11-22T15:01:00+00:00")  # UTC で保存
        self.assertEqual(r["estimated_cost_usd"], 0.06)
        self.assertEqual(self.rows("SELECT run_id FROM announce_tweets")[0]["run_id"], result["run_id"])


if __name__ == "__main__":
    unittest.main()
