"""
database.py
------------
実行ごとの記録を SQLite（data/bot.db）に残すモジュール。

データ設計（docs/data_design.md）の「事実を表すデータ」と「実行記録」を保存する。
  ① targets         対象者マスタ
  ② cover_videos    歌ってみた動画
  ③ announce_tweets 告知ツイート（リツイートしたもののみ）
  ⑦ runs            実行記録（概要）

処理のためのデータ（検索の進み具合・保留候補など）は、これまでどおり JSON に置く。
このデータベースは記録用で、Bot の判定には使わない（壊れても Bot は止まらない）。

公開リポジトリにコミットされるため、ツイートの本文は保存しない。
日時はすべて UTC の ISO 8601 文字列で保存する。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .core import DATA_PATH

DB_FILE = DATA_PATH / "bot.db"
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    x_username         TEXT PRIMARY KEY,   -- X のアカウント名
    x_user_id          TEXT,               -- X のユーザー ID（分かったものだけ）
    name               TEXT,               -- 表示名（公式サイトから）
    kind               TEXT NOT NULL,      -- talent（YouTube あり）/ other（ユニット・公式など）
    youtube_channel_id TEXT,
    official_slug      TEXT,               -- 公式サイトのタレントページの識別子
    active             INTEGER NOT NULL DEFAULT 1,  -- following.json に入っているか
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cover_videos (
    video_id     TEXT PRIMARY KEY,         -- YouTube の動画 ID
    x_username   TEXT,                     -- 公開した対象者（分からなければ NULL）
    channel_id   TEXT,
    title        TEXT,
    published_at TEXT,                     -- RSS の公開日時（X 経由で知った動画は NULL）
    detected_at  TEXT NOT NULL,            -- 最初に記録した日時
    detected_by  TEXT NOT NULL             -- rss（YouTube の RSS）/ x（告知ツイートから）
);

CREATE TABLE IF NOT EXISTS announce_tweets (
    tweet_id         TEXT PRIMARY KEY,     -- X のツイート ID
    video_id         TEXT NOT NULL,        -- 告知している動画（代表の 1 本）
    x_username       TEXT,                 -- 投稿した対象者（分からなければ NULL）
    author_id        TEXT,                 -- X のユーザー ID
    tweet_created_at TEXT,                 -- 投稿日時（ツイート ID から求める）
    retweeted_at     TEXT,                 -- リツイートした日時（過去分の取り込みでは NULL のことがある）
    found_by         TEXT,                 -- keyword / youtube_url / full_scan（不明は NULL）
    run_id           INTEGER               -- リツイートした実行（runs への参照）
);

CREATE TABLE IF NOT EXISTS runs (
    run_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    account                 TEXT,
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    status                  TEXT,          -- completed / error
    window_start_utc        TEXT,
    window_end_utc          TEXT,
    search_requests         INTEGER,
    posts_read              INTEGER,
    youtube_search_requests INTEGER,
    youtube_posts_read      INTEGER,
    retweets                INTEGER,
    pending_tweets          INTEGER,
    new_cover_videos        INTEGER,
    estimated_cost_usd      REAL,
    summary_json            TEXT           -- 概要の全体（観測記録の summary と同じ）
);

CREATE INDEX IF NOT EXISTS idx_cover_videos_user ON cover_videos (x_username);
CREATE INDEX IF NOT EXISTS idx_announce_tweets_video ON announce_tweets (video_id);
"""

# X のツイート ID（Snowflake）から投稿日時を求めるための基準（ミリ秒）
_TWITTER_EPOCH_MS = 1288834974657


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def tweet_time(tweet_id: str) -> Optional[str]:
    """ツイート ID から投稿日時（UTC）を求める。"""
    try:
        ms = (int(tweet_id) >> 22) + _TWITTER_EPOCH_MS
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """データベースを開く（なければ作る）。"""
    p = Path(path or DB_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


# ----------------------------------------------------------------------
# ① 対象者マスタ
# ----------------------------------------------------------------------
def sync_targets(
    conn: sqlite3.Connection,
    following: Iterable[str],
    channels: Dict[str, str],
    now: datetime,
) -> None:
    """
    following.json と youtube_channels.json の内容を対象者マスタに反映する。

    following.json から外れたアカウントは消さずに active = 0 にする（過去の記録とつなぐため）。
    表示名・公式サイトの識別子は update_profiles（build_youtube_channels.py）で入れる。
    """
    ts = _iso(now)
    users = list(dict.fromkeys(following))
    conn.execute("UPDATE targets SET active = 0, updated_at = ? WHERE active = 1", (ts,))
    for u in users:
        cid = channels.get(u)
        conn.execute(
            """
            INSERT INTO targets (x_username, kind, youtube_channel_id, active, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT (x_username) DO UPDATE SET
                kind = excluded.kind,
                youtube_channel_id = excluded.youtube_channel_id,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (u, "talent" if cid else "other", cid, ts),
        )


def update_profiles(conn: sqlite3.Connection, profiles: Iterable[Dict[str, str]], now: datetime) -> int:
    """
    公式サイトの情報（x_username・name・slug・youtube_channel_id）を対象者マスタに入れる。

    Returns:
        更新した件数。
    """
    ts = _iso(now)
    n = 0
    for p in profiles:
        conn.execute(
            """
            INSERT INTO targets (x_username, name, kind, youtube_channel_id, official_slug, active, updated_at)
            VALUES (:x_username, :name, 'talent', :youtube_channel_id, :slug, 1, :ts)
            ON CONFLICT (x_username) DO UPDATE SET
                name = excluded.name,
                kind = 'talent',
                youtube_channel_id = excluded.youtube_channel_id,
                official_slug = excluded.official_slug,
                updated_at = excluded.updated_at
            """,
            {**p, "ts": ts},
        )
        n += 1
    return n


# ----------------------------------------------------------------------
# ② 歌ってみた動画
# ----------------------------------------------------------------------
def record_cover_videos(conn: sqlite3.Connection, videos: Iterable[Dict[str, Any]], now: datetime) -> int:
    """
    RSS で見つけた歌ってみたを記録する（既にあればタイトルだけ最新にする）。

    videos の要素: {video_id, x_username, channel_id, title, published_at(datetime)}

    Returns:
        新しく記録した本数。
    """
    ts = _iso(now)
    n = 0
    for v in videos:
        cur = conn.execute(
            """
            INSERT INTO cover_videos (video_id, x_username, channel_id, title, published_at, detected_at, detected_by)
            VALUES (?, ?, ?, ?, ?, ?, 'rss')
            ON CONFLICT (video_id) DO NOTHING
            """,
            (v["video_id"], v.get("x_username"), v.get("channel_id"), v.get("title"), _iso(v.get("published_at")), ts),
        )
        if cur.rowcount:
            n += 1
        else:
            # X 経由で先に記録した動画は、RSS の情報で埋める
            conn.execute(
                """
                UPDATE cover_videos SET
                    title = ?,
                    x_username = COALESCE(x_username, ?),
                    channel_id = COALESCE(channel_id, ?),
                    published_at = COALESCE(published_at, ?)
                WHERE video_id = ?
                """,
                (v.get("title"), v.get("x_username"), v.get("channel_id"), _iso(v.get("published_at")), v["video_id"]),
            )
    return n


# ----------------------------------------------------------------------
# ③ 告知ツイート（リツイートしたもののみ）
# ----------------------------------------------------------------------
def _resolve_username(conn: sqlite3.Connection, video_id: str, author_id: Optional[str]) -> Optional[str]:
    """動画（②）またはユーザー ID（①）から、投稿した対象者を求める。"""
    row = conn.execute("SELECT x_username FROM cover_videos WHERE video_id = ?", (video_id,)).fetchone()
    if row and row["x_username"]:
        return row["x_username"]
    if author_id:
        row = conn.execute("SELECT x_username FROM targets WHERE x_user_id = ?", (author_id,)).fetchone()
        if row:
            return row["x_username"]
    return None


def _learn_user_id(conn: sqlite3.Connection, user: str, author_id: str) -> None:
    """
    対象者の X のユーザー ID を覚え、これまで対象者が分からなかった記録を埋める。

    過去分（データベースを作る前の記録など）は、ユーザー ID だけ分かっていて
    アカウント名が空のことがあるため、ここでさかのぼって埋める。
    """
    conn.execute(
        "UPDATE targets SET x_user_id = ? WHERE x_username = ? AND x_user_id IS NULL",
        (author_id, user),
    )
    conn.execute(
        "UPDATE announce_tweets SET x_username = ? WHERE author_id = ? AND x_username IS NULL",
        (user, author_id),
    )
    conn.execute(
        """
        UPDATE cover_videos SET x_username = ?
        WHERE x_username IS NULL AND video_id IN (SELECT video_id FROM announce_tweets WHERE author_id = ?)
        """,
        (user, author_id),
    )


def record_retweets(
    conn: sqlite3.Connection,
    retweets: Iterable[Dict[str, Any]],
    run_id: Optional[int],
    now: datetime,
) -> int:
    """
    リツイートした告知を記録する。

    retweets の要素: {tweet_id, video_id, author_id, found_by, retweeted_at}
    retweeted_at は datetime。キーを省略すると now、None を渡すと「不明」（NULL）になる。

    - 動画が②にない場合（RSS の対象外のチャンネルなど）は、②に detected_by = 'x' で加える
    - 投稿した対象者が分かったら、①に X のユーザー ID を覚えておく（次回以降の対応づけに使う）

    Returns:
        新しく記録した件数。
    """
    ts = _iso(now)
    n = 0
    for r in retweets:
        tid = str(r["tweet_id"])
        vid = str(r["video_id"])
        author_id = str(r["author_id"]) if r.get("author_id") else None
        user = _resolve_username(conn, vid, author_id)

        conn.execute(
            """
            INSERT INTO cover_videos (video_id, x_username, detected_at, detected_by)
            VALUES (?, ?, ?, 'x')
            ON CONFLICT (video_id) DO UPDATE SET x_username = COALESCE(cover_videos.x_username, excluded.x_username)
            """,
            (vid, user, ts),
        )
        cur = conn.execute(
            """
            INSERT INTO announce_tweets
                (tweet_id, video_id, x_username, author_id, tweet_created_at, retweeted_at, found_by, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tweet_id) DO NOTHING
            """,
            (
                tid, vid, user, author_id, tweet_time(tid),
                _iso(r["retweeted_at"]) if "retweeted_at" in r else ts,  # 明示的な None は「不明」
                r.get("found_by"), run_id,
            ),
        )
        n += cur.rowcount
        if user and author_id:
            _learn_user_id(conn, user, author_id)
    return n


# ----------------------------------------------------------------------
# ⑦ 実行記録
# ----------------------------------------------------------------------
def record_run(
    conn: sqlite3.Connection,
    run: Dict[str, Any],
    summary: Dict[str, Any],
    new_cover_videos: Optional[int] = None,
) -> int:
    """
    実行 1 回分の概要を記録し、run_id を返す。

    Args:
        run: 観測記録（RunRecorder.run）。account・started_at・status などを使う。
        summary: RunRecorder.summary() の結果。
    """
    def utc(s: Optional[str]) -> Optional[str]:
        return _iso(datetime.fromisoformat(s)) if s else None

    window = run.get("search_window") or {}
    cur = conn.execute(
        """
        INSERT INTO runs (
            account, started_at, finished_at, status, window_start_utc, window_end_utc,
            search_requests, posts_read, youtube_search_requests, youtube_posts_read,
            retweets, pending_tweets, new_cover_videos, estimated_cost_usd, summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.get("account"),
            utc(run.get("started_at")),
            utc(run.get("finished_at")),
            run.get("status"),
            window.get("start_utc"),
            window.get("end_utc"),
            summary.get("search_requests"),
            summary.get("posts_read"),
            summary.get("youtube_search_requests"),
            summary.get("youtube_posts_read"),
            summary.get("retweets"),
            summary.get("pending_tweets"),
            new_cover_videos,
            (summary.get("estimated_cost_usd") or {}).get("total"),
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
        ),
    )
    return int(cur.lastrowid)


def record_execution(
    run: Dict[str, Any],
    summary: Dict[str, Any],
    following: List[str],
    channels: Dict[str, str],
    cover_videos: List[Dict[str, Any]],
    retweets: List[Dict[str, Any]],
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> Dict[str, int]:
    """
    Bot の 1 回の実行を、1 つのトランザクションでまとめて記録する。

    Returns:
        {"run_id", "new_cover_videos", "new_announce_tweets"}
    """
    now = now or datetime.now(timezone.utc)
    conn = connect(path)
    try:
        with conn:
            sync_targets(conn, following, channels, now)
            new_videos = record_cover_videos(conn, cover_videos, now)
            run_id = record_run(conn, run, summary, new_videos)
            new_tweets = record_retweets(conn, retweets, run_id, now)
        return {"run_id": run_id, "new_cover_videos": new_videos, "new_announce_tweets": new_tweets}
    finally:
        conn.close()
