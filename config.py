import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class TwitterAuth:
    api_key: str
    api_secret: str
    access_token: str
    access_secret: str
    bearer_token: str


def load_twitter_auth(index: int = 1) -> TwitterAuth:
    """
    環境変数から Twitter API 認証情報を読み込み、
    必須情報がすべて揃っていることを保証したうえで
    TwitterAuth オブジェクトとして返す。

    本関数は複数アカウント運用を想定し、
    環境変数名の末尾に 2 桁の番号を付与する方式を採用している。
    例として、index=1 の場合は以下の環境変数を参照する。

      - API_KEY_01
      - API_SECRET_01
      - ACCESS_TOKEN_01
      - ACCESS_SECRET_01
      - BEARER_TOKEN_01

    `os.getenv()` の戻り値は仕様上 `str | None` であるため、
    本関数では内部ヘルパー関数を用いて
    「必ず str を返す」ことを型・実行時の両面で保証する。

    Args:
        index (int):
            使用する Twitter アカウント番号。
            1 以上の整数を想定する。
            デフォルトは 1。

    Returns:
        TwitterAuth:
            すべての認証情報が `str` として揃った状態の
            TwitterAuth オブジェクト。

    Raises:
        ValueError:
            対応する環境変数のいずれかが未設定、
            もしくは空文字である場合に送出される。
            設定漏れを起動時に即座に検出することを目的とする。
    """

    def require_env(name: str) -> str:
        """
        必須環境変数を取得し、値が存在しない場合は例外を送出する。

        Args:
            name (str):
                取得対象の環境変数名。

        Returns:
            str:
                環境変数の値。
                正常終了時、この戻り値は必ず str 型である。

        Raises:
            ValueError:
                環境変数が未定義、または空文字の場合。
        """
        value = os.getenv(name)
        if value is None or value == "":
            raise ValueError(f"環境変数 {name} が設定されていません。")
        return value

    suffix = f"_{index:02d}"

    api_key = require_env(f"API_KEY{suffix}")
    api_secret = require_env(f"API_SECRET{suffix}")
    access_token = require_env(f"ACCESS_TOKEN{suffix}")
    access_secret = require_env(f"ACCESS_SECRET{suffix}")
    bearer_token = require_env(f"BEARER_TOKEN{suffix}")

    return TwitterAuth(
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
        bearer_token=bearer_token,
    )

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ACCESS_SECRET = os.getenv("ACCESS_SECRET")
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

MAX_USERS_PER_SEARCH = 6                        # 一度に検索するユーザー数
KEYWORDS = ["cover", "歌ってみた"]               # 検索キーワード
LOOKBACK_DAYS = 1                               # 遡る日数
RETWEET_LIMIT = 5                               # 1日あたりのリツイート上限
MAX_TWEETS_PER_SEARCH = 10                      # 一度の検索で取得する最大ツイート数

# フォルダ設定
DATA_DIR = "data"
LOG_DIR = "logs"
