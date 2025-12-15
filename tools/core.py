"""
core.py
--------
共通の基盤ユーティリティ:
 - ログ出力
 - ディレクトリ・ファイルパスの初期化
"""

from pathlib import Path
from datetime import datetime
from config import DATA_DIR, LOG_DIR
from datetime import datetime, timedelta, timezone

# --- パス設定 ---
DATA_PATH = Path(DATA_DIR)
LOG_PATH = Path(LOG_DIR)

FOLLOWING_FILE = DATA_PATH / "following.json"
RETWEETED_FILE = DATA_PATH / "retweeted.json"
RETWEET_LOG_FILE = LOG_PATH / "retweeting.log"

# --- ディレクトリ初期化 ---
DATA_PATH.mkdir(parents=True, exist_ok=True)
LOG_PATH.mkdir(parents=True, exist_ok=True)

# --- ログユーティリティ ---
def log_message(log_file: Path, message: str) -> None:
    """指定ログファイルに追記出力し、標準出力にも出す"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line)
    print(message)

# ------------------------------------------------------------
# 前日の日付範囲取得
# ------------------------------------------------------------
def get_previous_day_range_utc() -> tuple[datetime, datetime]:
    """
    日本時間の「昨日 0:00」〜「本日 0:00」の時刻範囲を、
    そのまま UTC に変換して返す。
    （Twitter API の日時指定に直接使用できる形式）
    """

    JST = timezone(timedelta(hours=9))
    
    # 日本時間の現在日時
    now_jst = datetime.now(JST)

    # 日付部分だけ取得（JST）
    today_jst_date = now_jst.date()
    yesterday_jst_date = today_jst_date - timedelta(days=1)

    # JST の昨日 0:00
    start_jst = datetime(
        year=yesterday_jst_date.year,
        month=yesterday_jst_date.month,
        day=yesterday_jst_date.day,
        hour=0, minute=0, second=0,
        tzinfo=JST
    )

    # JST の今日 0:00
    end_jst = datetime(
        year=today_jst_date.year,
        month=today_jst_date.month,
        day=today_jst_date.day,
        hour=0, minute=0, second=0,
        tzinfo=JST
    )

    # UTC に変換
    start_utc = start_jst.astimezone(timezone.utc)
    end_utc = end_jst.astimezone(timezone.utc)

    return start_utc, end_utc