"""
connection_check.py
-------------------
課金クエリを使用せず Twitter API 接続確認のみを行うスクリプト
"""

from tools.twitter_api import create_client, verify_connection


def main():
    # サフィックスなし（DEFAULTアカウント）
    client = create_client()

    success = verify_connection(client)

    if success:
        print("API認証は正常です。")
    else:
        print("API認証に問題があります。")


if __name__ == "__main__":
    main()
