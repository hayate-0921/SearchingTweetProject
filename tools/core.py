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
def log_message(log_file: Path, message: str, print_to_console:bool = True) -> None:
    """
    指定されたログファイルへメッセージを追記し、
    同一内容を標準出力（コンソール）にも出力するログユーティリティ関数。

    本関数は以下の責務を持つ。
    - ログファイルの親ディレクトリが存在しない場合、自動的に作成する
    - ISO 8601 形式のタイムスタンプを付与してログを追記する
    - 実行時の可視性確保のため、同じメッセージを標準出力にも出す

    Args:
        log_file (Path):
            出力先となるログファイルのパス。
            親ディレクトリが存在しない場合でも安全に使用できるよう、
            内部で `mkdir(parents=True, exist_ok=True)` を実行する。
        message (str):
            ログとして記録したいメッセージ本文。
            改行は内部で付与されるため、通常は末尾の改行を含める必要はない。
        print_to_console (bool):
            Trueの場合、ファイルに記録するメッセージと同内容をプリントする。
            デフォルトはTrue。

    Returns:
        None:
            本関数は値を返さず、副作用（ファイル出力・標準出力）のみを行う。

    Side Effects:
        - ログファイルの親ディレクトリを作成する可能性がある
        - ログファイルへ追記書き込みを行う
        - 標準出力へメッセージを出力する

    Raises:
        OSError:
            ディレクトリ作成やファイル書き込みに失敗した場合。
            本関数では例外を捕捉せず、そのまま呼び出し元へ送出する設計とする。
            （上位レイヤーで一括して異常終了・ログ出力を行うことを想定）
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}\n"

    with log_file.open("a", encoding="utf-8") as f:
        f.write(line)
    if print_to_console:
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