import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
ACCESS_SECRET = os.getenv("ACCESS_SECRET")
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

MAX_USERS_PER_SEARCH = 6                        # 一度に検索するユーザー数
KEYWORDS = ["cover", "歌ってみた"]               # 検索キーワード
LOOKBACK_DAYS = 1                               # 遡る日数
RETWEET_LIMIT = 5                               # 1日あたりのリツイート上限
MAX_TWEETS_PER_SEARCH = 5                       # 一度の検索で取得する最大ツイート数

# フォルダ設定
DATA_DIR = "data"
LOG_DIR = "logs"
