"""
Phase 3（YouTube を起点にした取りこぼし対策）のテスト（X API・YouTube には接続しない）。

実行方法（プロジェクトのルートで）:
    python -m unittest discover -s tests -v

2026-11-15〜21 の実例をもとにしている。
  - 緋八マナ: 告知に検索ワードがない（「24時からプレミア公開！」）→ YouTube リンクの検索で見つかる
  - Scarle Yonaguni: X 側で URL が検索に当たらない → 全件読みで見つかる
  - Klara Charmwood: X で告知していない → 見つからない（何もリツイートしない）
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
_TMP = tempfile.mkdtemp(prefix="youtube_test_")
_OLD_CWD = os.getcwd()

UTC = timezone.utc
NOW = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)  # JST 11/21 15:00
END = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)  # JST 11/21 0:00

COVER = ""
EXCLUDE = ""


def setUpModule() -> None:
    global COVER, EXCLUDE
    (Path(_TMP) / "data").mkdir()
    shutil.copy(ROOT / "data" / "following.json", Path(_TMP) / "data" / "following.json")
    os.chdir(_TMP)
    sys.path.insert(0, str(ROOT))
    for k in ["API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "BEARER_TOKEN"]:
        os.environ.setdefault(k, "dummy")
    import config

    COVER, EXCLUDE = config.YOUTUBE_COVER_TITLE_PATTERN, config.YOUTUBE_EXCLUDE_TITLE_PATTERN


def tearDownModule() -> None:
    os.chdir(_OLD_CWD)
    shutil.rmtree(_TMP, ignore_errors=True)


def tweet(tid, created_at, urls, text="告知"):
    ents = [{"url": "https://t.co/x", "expanded_url": u} for u in urls]
    return NS(id=int(tid), text=text, author_id=1, created_at=created_at, entities={"urls": ents})


def feed_xml(entries):
    items = "".join(
        f"""<entry><yt:videoId>{vid}</yt:videoId><title>{title}</title>
        <link rel="alternate" href="{link}"/><published>{pub}</published></entry>"""
        for vid, title, link, pub in entries
    )
    return (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015">' + items + "</feed>"
    ).encode("utf-8")


def fv(vid, title, published, channel="UC_MANA", short=False):
    from tools.youtube_feed import FeedVideo

    link = f"https://www.youtube.com/{'shorts/' if short else 'watch?v='}{vid}"
    return FeedVideo(vid, channel, title, published, link)


# ----------------------------------------------------------------------
# RSS とタイトル判定
# ----------------------------------------------------------------------
class TestFeed(unittest.TestCase):
    def test_parse_feed(self):
        from tools.youtube_feed import parse_feed

        xml = feed_xml(
            [
                ("Ss_Jj35EG18", "【歌ってみた】花が落ちたので、/一二三【にじさんじ/緋八マナ】",
                 "https://www.youtube.com/watch?v=Ss_Jj35EG18", "2026-11-20T15:00:00+00:00"),
                ("ddjFVChAfzQ", "Yutapon先生のお言葉 #shorts",
                 "https://www.youtube.com/shorts/ddjFVChAfzQ", "2026-11-21T02:00:00+00:00"),
            ]
        )
        videos = parse_feed(xml, "UC_MANA")
        self.assertEqual([v.video_id for v in videos], ["Ss_Jj35EG18", "ddjFVChAfzQ"])
        self.assertFalse(videos[0].is_short)
        self.assertTrue(videos[1].is_short)
        self.assertEqual(videos[0].published, datetime(2026, 11, 20, 15, 0, tzinfo=UTC))

    def test_cover_title(self):
        from tools.youtube_feed import is_cover_title as c

        yes = [
            "【歌ってみた】花が落ちたので、/一二三【にじさんじ/緋八マナ】",
            "QUEEN/Kanaria ver. Scarle Yonaguni",
            "黒のバースデイ / Black Birthday【Cover by Klara Charmwood】",
            "しあわせレストラン / 弌誠 Covered by 塚原大地【にじさんじ】",
            "ジョジョ〜その血の運命〜 / 富永TOMMY弘明【加賀美ハヤト Cover】",
            "Alban Knox x Sonny Brisko - magnet -recall- (WANNYANZ ver.)",
        ]
        no = [
            "【歌枠】カバー曲いっぱい歌う！【にじさんじ】",
            "【Karaoke】cover songs all night",
            "【APEX】ランク【塚原大地 / にじさんじ】",
            "「だって。」弦月藤士郎 feat.音街ウナ V4",
            "【 ゼンゼロ 】予告番組 Ver.3.4「ついには長き、日の終わり」同時視聴",  # 版数の ver. は除く
        ]
        for t in yes:
            self.assertTrue(c(t, COVER, EXCLUDE), t)
        for t in no:
            self.assertFalse(c(t, COVER, EXCLUDE), t)


# ----------------------------------------------------------------------
# 監視リストと検索計画
# ----------------------------------------------------------------------
class TestWatchAndPlan(unittest.TestCase):
    def test_update_watch(self):
        from tools.youtube_fallback import update_watch

        watch = {
            "OLD": {"username": "a", "channel_id": "UC_A", "published": (NOW - timedelta(days=8)).isoformat()},
            "DONE": {"username": "a", "channel_id": "UC_A", "published": NOW.isoformat()},
        }
        videos = [
            fv("Ss_Jj35EG18", "【歌ってみた】花が落ちたので、", NOW - timedelta(hours=15)),
            fv("SHORT", "歌ってみた #shorts", NOW, short=True),
            fv("GAME", "【APEX】ランク", NOW),
            fv("RT", "cover", NOW),
            fv("OTHER", "cover", NOW, channel="UC_UNKNOWN"),
        ]
        added = update_watch(
            watch, videos, {"UC_MANA": "Hibachi_Mana"}, {"DONE", "RT"}, NOW, COVER, EXCLUDE, 7
        )
        self.assertEqual(added, ["Ss_Jj35EG18"])
        self.assertEqual(set(watch), {"Ss_Jj35EG18"})  # 期限切れとリツイート済みは外れる
        self.assertEqual(watch["Ss_Jj35EG18"]["username"], "Hibachi_Mana")
        self.assertIsNone(watch["Ss_Jj35EG18"]["searched_until_utc"])

    def test_plan(self):
        from tools.youtube_fallback import plan_searches

        pub = datetime(2026, 11, 19, 10, 30, tzinfo=UTC)
        watch = {
            "V1": {"username": "H_KAGAMI2434", "published": pub.isoformat(), "searched_until_utc": None},
            "V2": {"username": "ScarleYonaguni", "published": (NOW - timedelta(days=6, hours=20)).isoformat(),
                   "searched_until_utc": (END - timedelta(days=1)).isoformat()},
            "V3": {"username": "KlaraCharmwood", "published": pub.isoformat(), "searched_until_utc": END.isoformat()},
            "V4": {"username": "Kanae_2434", "published": pub.isoformat(), "searched_until_utc": None},
            "V5": {"username": "Ruri_4ori8", "published": pub.isoformat(), "status": "found"},
        }
        plans, skipped = plan_searches(watch, {"V4"}, END, NOW, 3, 10)
        by_user = {p.username: p for p in plans}
        # V3 は探し終わっている、V4 は通常の検索で見つかった、V5 は告知が見つかり済み
        self.assertEqual(set(by_user), {"H_KAGAMI2434", "ScarleYonaguni"})
        # 初回は公開の 3 日前から（プレミアの予告を拾うため）
        self.assertEqual(by_user["H_KAGAMI2434"].start, pub - timedelta(days=3))
        # 2 回目以降は前回の続きから
        self.assertEqual(by_user["ScarleYonaguni"].start, END - timedelta(days=1))
        self.assertEqual(plans[0].username, "ScarleYonaguni")  # 公開の早い順
        self.assertEqual(skipped, [])

        plans, skipped = plan_searches(watch, {"V4"}, END, NOW, 3, 1)
        self.assertEqual([p.username for p in plans], ["ScarleYonaguni"])
        self.assertEqual(skipped, ["H_KAGAMI2434"])

    def test_plan_clamps_to_seven_days(self):
        from tools.youtube_fallback import plan_searches

        watch = {"V": {"username": "u", "published": (NOW - timedelta(days=6)).isoformat(), "searched_until_utc": None}}
        plans, _ = plan_searches(watch, set(), END, NOW, 3, 10)
        self.assertEqual(plans[0].start, NOW - timedelta(days=7) + timedelta(minutes=5))


# ----------------------------------------------------------------------
# 告知探し
# ----------------------------------------------------------------------
class TestFindAnnouncements(unittest.TestCase):
    def setUp(self):
        t = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
        # X 側の投稿（url_indexed=False は url: 条件に当たらない投稿）
        self.posts = {
            "Hibachi_Mana": [
                (tweet(1, t, ["https://youtu.be/Ss_Jj35EG18?si=x"], "24時からプレミア公開！"), True),
                (tweet(2, t, ["https://www.youtube.com/live/hAhYNRsoleQ"], "配信開始"), True),
                (tweet(3, t, [], "おはよう"), True),
            ],
            "ScarleYonaguni": [
                (tweet(11, t, ["https://youtube.com/watch?v=H40xNZL_q2Q"], "NEW COVER LIVE NOW"), False),
                (tweet(12, t, ["https://youtube.com/watch?v=te5IhMBncZM"], "LIVE NOW"), False),
                (tweet(13, t, [], ":3c"), True),
            ],
            "KlaraCharmwood": [
                (tweet(21, t, ["https://youtube.com/shorts/rAMG7skrZqo"], "Dance Cover"), True),
                (tweet(22, t, [], "Happy Birthday!"), True),
            ],
        }
        self.queries = []
        self.windows = []

    def search_fn(self, query, start_time, end_time, page_size, max_pages):
        self.queries.append(query)
        self.windows.append((start_time, end_time))
        user = query.split()[0][len("from:"):]
        posts = self.posts.get(user, [])
        if 'url:"youtube.com"' in query:
            data = [tw for tw, indexed in posts if indexed and tw.entities["urls"]]
        else:
            data = [tw for tw, _ in posts]
        return data, {"result_count": len(data), "pages": 1, "next_token": None}

    def plan(self, user, vids):
        from tools.youtube_fallback import UserSearch

        return UserSearch(user, vids, END - timedelta(days=3), END)

    def test_found_by_youtube_link(self):
        from tools.youtube_fallback import find_announcements

        p = self.plan("Hibachi_Mana", ["Ss_Jj35EG18"])
        found, logs = find_announcements([p], self.search_fn, 100)
        self.assertEqual([str(t.id) for t in found], ["1"])
        self.assertEqual(p.found, {"Ss_Jj35EG18": "1"})
        self.assertEqual([lg.step for lg in logs], ["youtube_url"])  # 全件読みはしない

    def test_found_by_full_scan(self):
        from tools.youtube_fallback import find_announcements

        p = self.plan("ScarleYonaguni", ["H40xNZL_q2Q"])
        found, logs = find_announcements([p], self.search_fn, 100)
        self.assertEqual([str(t.id) for t in found], ["11"])  # 無関係な投稿（12, 13）は候補にしない
        self.assertEqual([lg.step for lg in logs], ["youtube_url", "full_scan"])
        self.assertEqual(logs[1].meta["result_count"], 3)  # 費用は読んだ件数で記録する

    def test_full_scan_window_is_narrowed(self):
        from tools.youtube_fallback import UserSearch, find_announcements

        pub = END - timedelta(days=6)
        p = UserSearch("ScarleYonaguni", ["H40xNZL_q2Q"], pub - timedelta(days=1), END, latest_published=pub)
        find_announcements([p], self.search_fn, 100, full_scan_after_days=1)
        # 1 段目は期間全体、全件読みは公開の 1 日後まで
        self.assertEqual(self.windows[0], (pub - timedelta(days=1), END))
        self.assertEqual(self.windows[1], (pub - timedelta(days=1), pub + timedelta(days=1)))

    def test_not_announced(self):
        from tools.youtube_fallback import find_announcements

        p = self.plan("KlaraCharmwood", ["eqrD1Ocof7U"])
        found, logs = find_announcements([p], self.search_fn, 100)
        self.assertEqual(found, [])
        self.assertEqual(p.found, {})


# ----------------------------------------------------------------------
# Bot 全体（API・RSS は模擬）
# ----------------------------------------------------------------------
class TestBotWithYouTube(unittest.TestCase):
    def setUp(self):
        import retweeting_bot as rb

        for name in ["retweeted.json", "retweeted_videos.json", "pending.json", "state.json", "youtube_watch.json"]:
            p = Path("data") / name
            if p.exists():
                p.unlink()
        shutil.rmtree("data/observations", ignore_errors=True)
        (Path("data") / "youtube_channels.json").write_text(
            json.dumps({"Hibachi_Mana": "UC_MANA", "ScarleYonaguni": "UC_SCARLE", "Kanae_2434": "UC_KANAE"}),
            encoding="utf-8",
        )

        self.rb = rb
        self.retweets = []
        self.queries = []
        t = datetime.now(UTC) - timedelta(days=1)
        self.t = t
        pub = t - timedelta(hours=1)
        self.feed = [
            fv("Ss_Jj35EG18", "【歌ってみた】花が落ちたので、", pub, "UC_MANA"),
            fv("H40xNZL_q2Q", "QUEEN/Kanaria ver. Scarle Yonaguni", pub, "UC_SCARLE"),
            fv("tjROC5CQmAo", "????? / 叶 (cover)", pub, "UC_KANAE"),
        ]
        kanae = tweet(100, t, ["https://www.youtube.com/watch?v=tjROC5CQmAo"], "cover")
        mana = tweet(101, t, ["https://youtu.be/Ss_Jj35EG18"], "24時からプレミア公開！")
        scarle = tweet(102, t, ["https://youtube.com/watch?v=H40xNZL_q2Q"], "NEW COVER")
        other = tweet(103, t, [], "おはよう")

        def fake_search_all(client, query, start_time, end_time, page_size, max_pages):
            self.queries.append(query)
            if query.startswith("(from:"):  # 通常の検索: 叶さんの告知だけが当たる
                data = [kanae] if "from:Kanae_2434" in query else []
            elif query.startswith("from:Hibachi_Mana"):
                data = [mana]
            elif query.startswith("from:ScarleYonaguni") and 'url:"youtube.com"' not in query:
                data = [scarle, other]
            else:
                data = []
            return data, {"result_count": len(data), "pages": 1, "next_token": None}

        self._orig = (rb.search_all_tweets, rb.retweet, rb.create_client, rb.fetch_feeds)
        rb.search_all_tweets = fake_search_all
        rb.retweet = lambda client, tid: self.retweets.append(str(tid))
        rb.create_client = lambda suffix=None: object()
        rb.fetch_feeds = lambda ids, workers, timeout: (list(self.feed), {})

    def tearDown(self):
        rb = self.rb
        rb.search_all_tweets, rb.retweet, rb.create_client, rb.fetch_feeds = self._orig
        (Path("data") / "youtube_channels.json").unlink()

    def test_fallback_retweets_missed_announcements(self):
        from tools.data_manager import load_youtube_watch

        self.rb.run_retweet_task()
        # 叶さん（通常の検索）＋緋八さん・Scarle さん（YouTube 起点）の 3 件
        self.assertEqual(sorted(self.retweets), ["100", "101", "102"])
        yt_queries = [q for q in self.queries if not q.startswith("(from:")]
        # 叶さんは通常の検索で見つかったので探さない。緋八さんは 1 段目で見つかる
        self.assertFalse(any(q.startswith("from:Kanae_2434") for q in yt_queries))
        self.assertEqual(sum(q.startswith("from:Hibachi_Mana") for q in yt_queries), 1)
        self.assertEqual(sum(q.startswith("from:ScarleYonaguni") for q in yt_queries), 2)
        # リツイートした動画は監視リストから外れる
        self.assertEqual(load_youtube_watch(), {})

        obs = json.loads(next(Path("data/observations").glob("*.json")).read_text(encoding="utf-8"))
        run = obs["runs"][0]
        self.assertEqual(sorted(run["youtube"]["found_videos"]), ["H40xNZL_q2Q", "Ss_Jj35EG18"])
        self.assertEqual(run["summary"]["youtube_search_requests"], 3)
        self.assertEqual(run["summary"]["youtube_posts_read"], 3)
        self.assertEqual(run["summary"]["queries_not_executed"], 0)
        recorded = {t["id"] for t in run["tweets"]}
        self.assertNotIn("103", recorded)  # 無関係な投稿は記録しない


    def test_youtube_failure_does_not_stop_bot(self):
        def broken(ids, workers, timeout):
            raise OSError("network down")

        self.rb.fetch_feeds = broken
        self.rb.run_retweet_task()
        self.assertEqual(self.retweets, ["100"])  # 通常の検索分はリツイートされる


if __name__ == "__main__":
    unittest.main()
