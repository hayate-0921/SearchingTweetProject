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
from typing import Any, Dict, List, Set

from .core import (
    FOLLOWING_FILE,
    PENDING_FILE,
    RETWEETED_FILE,
    RETWEETED_VIDEOS_FILE,
    STATE_FILE,
)


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


# ----------------------------------------------------------------------
# 汎用 JSON 読み書き
# ----------------------------------------------------------------------
def _load_json(path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ----------------------------------------------------------------------
# リツイート済み動画 ID（動画単位の重複回避）
# ----------------------------------------------------------------------
def load_retweeted_videos() -> Dict[str, str]:
    """
    リツイート済みの YouTube 動画 ID を {動画 ID: リツイートしたツイート ID} で返す。
    ファイルが存在しない場合は空の辞書を返す。
    """
    return {str(k): str(v) for k, v in _load_json(RETWEETED_VIDEOS_FILE, {}).items()}


def save_retweeted_videos(videos: Dict[str, str]) -> None:
    """リツイート済み動画 ID を動画 ID 順に保存する。"""
    _save_json(RETWEETED_VIDEOS_FILE, dict(sorted(videos.items())))


# ----------------------------------------------------------------------
# 翌日に回した候補（リツイート上限超過分）
# ----------------------------------------------------------------------
def load_pending() -> List[Dict[str, Any]]:
    """
    前回の実行で上限を超えて見送った候補を返す。
    各要素は {"tweet_id": str, "video_ids": [str], "created_at": str | None}。
    """
    return list(_load_json(PENDING_FILE, []))


def save_pending(pending: List[Dict[str, Any]]) -> None:
    _save_json(PENDING_FILE, pending)


# ----------------------------------------------------------------------
# 実行状態（前回どこまで検索したか）
# ----------------------------------------------------------------------
def load_state() -> Dict[str, Any]:
    """{"last_searched_until_utc": ISO 8601 文字列} などの実行状態を返す。"""
    return dict(_load_json(STATE_FILE, {}))


def save_state(state: Dict[str, Any]) -> None:
    _save_json(STATE_FILE, state)
