"""
data_manager.py
----------------
ローカル JSON ファイル（data ディレクトリ内）に対する読み書きのみを担当するモジュール。

責務:
  - フォローリスト（username）の読み込み
  - リツイート済みツイート ID の保存/読み込み（重複回避用）

注意:
  - Tweepy や API 呼び出しは含まれません。API 呼び出しは twitter_api.py が担当します。
  - ファイルパスは `.core` 等で定義されたパス定数（FOLLOWING_FILE, RETWEETED_FILE）を用いる想定です。
"""

import json
from typing import List, Set

from .core import FOLLOWING_FILE, RETWEETED_FILE


# ----------------------------------------------------------------------
# フォローリスト JSON の読み込み
# ----------------------------------------------------------------------
def load_following_list() -> List[str]:
    """
    保存済みのフォロー一覧（username のリスト）を JSON から読み込んで返す。

    Returns:
        List[str]: username のリスト。
                   ファイルが存在しない場合は空リストを返す。

    Raises:
        OSError / json.JSONDecodeError: ファイル読み込み/パースに失敗した場合。
    """
    if not FOLLOWING_FILE.exists():
        return []

    with FOLLOWING_FILE.open("r", encoding="utf-8") as f:
        following_list = json.load(f)
        # print(following_list)
        print("following_listを読み込みました。")
        return following_list


# ----------------------------------------------------------------------
# リツイート済 ID 管理（重複回避）
# ----------------------------------------------------------------------
def load_retweeted_list() -> Set[str]:
    """
    保存済みのリツイート済みツイート ID を読み込み、集合として返す。
    ファイルが存在しない場合は空集合を返す。
    """
    if not RETWEETED_FILE.exists():
        return set()

    with RETWEETED_FILE.open("r", encoding="utf-8") as f:
        arr = json.load(f)

    return set(str(x) for x in arr)


def save_retweeted_list(retweeted_ids: Set[str]) -> None:
    """
    リツイート済みツイート ID の集合を JSON ファイルへ保存する。
    """
    with RETWEETED_FILE.open("w", encoding="utf-8") as f:
        json.dump(sorted(list(retweeted_ids)), f, ensure_ascii=False, indent=2)
