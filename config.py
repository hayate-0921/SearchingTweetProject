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



def load_twitter_auth(suffix: str | None = None) -> TwitterAuth:
    """
    環境変数から Twitter API 認証情報を読み込み、
    必須情報がすべて揃っていることを保証したうえで
    TwitterAuth オブジェクトとして返す。

    本関数は、以下 2 つの運用形態に対応する。

    1. 単一アカウント運用（suffix=None の場合）
       環境変数名はサフィックスを持たず、以下を参照する。

         - API_KEY
         - API_SECRET
         - ACCESS_TOKEN
         - ACCESS_SECRET
         - BEARER_TOKEN

    2. 複数アカウント運用（suffix に識別子を指定する場合）
       環境変数名の末尾に、意味を持つ識別用サフィックスを付与する。

       例: suffix="COVER" の場合

         - API_KEY_COVER
         - API_SECRET_COVER
         - ACCESS_TOKEN_COVER
         - ACCESS_SECRET_COVER
         - BEARER_TOKEN_COVER

    `os.getenv()` の戻り値は仕様上 `str | None` であるため、
    本関数では内部ヘルパー関数を用いて
    「必ず str を返す」ことを型・実行時の両面で保証する。

    Args:
        suffix (str | None):
            環境変数名の末尾に付与する識別子。
            None を指定した場合はサフィックスなしの環境変数名を使用する。

    Returns:
        TwitterAuth:
            すべての認証情報が `str` 型として揃った状態の
            TwitterAuth オブジェクト。

    Raises:
        ValueError:
            以下のいずれかに該当する場合に送出される。
            - suffix が空文字列で指定された場合
            - 必要な環境変数が未定義、または空文字である場合

            設定ミスや設定漏れをアプリケーション起動時に
            即座に検出することを目的とする。
    """

    if suffix == "":
        raise ValueError("suffix に空文字列は指定できません。")

    suffix_part = f"_{suffix}" if suffix is not None else ""

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

    api_key = require_env(f"API_KEY{suffix_part}")
    api_secret = require_env(f"API_SECRET{suffix_part}")
    access_token = require_env(f"ACCESS_TOKEN{suffix_part}")
    access_secret = require_env(f"ACCESS_SECRET{suffix_part}")
    bearer_token = require_env(f"BEARER_TOKEN{suffix_part}")

    return TwitterAuth(
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
        bearer_token=bearer_token,
    )



# アカウントごとに異なる定数
KEYWORDS = ["cover", "covered", "歌ってみた"]               # 検索キーワード

KEYWORDS_COVER = ["cover", "covered", "歌ってみた"]         # COVERアカウント用検索キーワード
KEYWORDS_ORIGINAL = ["オリジナル曲", "original"] # ORIGINALアカウント用検索キーワード
KEYWORDS_STREAM = ["カラオケ", "歌枠"]           # STREAMアカウント用検索キーワード

EXCLUDE_KEYWORDS = ["万再生", "万回再生", ]   # 除外キーワード

# 全体共通定数
MAX_USERS_PER_SEARCH = 10                        # 一度に検索するユーザー数
LOOKBACK_DAYS = 1                               # 遡る日数
RETWEET_LIMIT = 5                               # 1日あたりのリツイート上限
MAX_TWEETS_PER_SEARCH = 10                      # 一度の検索で取得する最大ツイート数

# フォルダ設定
DATA_DIR = "data"
LOG_DIR = "logs"
