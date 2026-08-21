"""X API v2 への投稿クライアント（OAuth 1.0a ユーザーコンテキスト）。"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from requests_oauthlib import OAuth1

TWEETS_ENDPOINT = "https://api.x.com/2/tweets"

# X の文字数カウントは全角/絵文字を2文字として数える（twitter-text の重み設定）。
X_MAX_WEIGHTED_LENGTH = 280
_SINGLE_WEIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))

_ENV_VARS = (
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
)


class XApiError(Exception):
    """X API 呼び出しが失敗したときに送出する。"""


class MissingCredentialsError(XApiError):
    """認証情報の環境変数が足りないときに送出する。"""


@dataclass(frozen=True)
class XCredentials:
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "XCredentials":
        source = env if env is not None else dict(os.environ)
        missing = [name for name in _ENV_VARS if not source.get(name)]
        if missing:
            raise MissingCredentialsError(
                "X の認証情報が設定されていません: " + ", ".join(missing)
            )
        return cls(
            api_key=source["X_API_KEY"],
            api_secret=source["X_API_SECRET"],
            access_token=source["X_ACCESS_TOKEN"],
            access_token_secret=source["X_ACCESS_TOKEN_SECRET"],
        )


class XClient:
    """X API v2 の POST /2/tweets を叩く最小クライアント。"""

    def __init__(
        self,
        credentials: XCredentials,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout = timeout

    def post_tweet(self, text: str) -> str:
        """投稿してツイートIDを返す。"""
        validate_length(text)
        auth = OAuth1(
            self.credentials.api_key,
            client_secret=self.credentials.api_secret,
            resource_owner_key=self.credentials.access_token,
            resource_owner_secret=self.credentials.access_token_secret,
        )
        try:
            response = self.session.post(
                TWEETS_ENDPOINT, json={"text": text}, auth=auth, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise XApiError(f"X API への接続に失敗しました: {exc}") from exc

        if response.status_code == 429:
            reset = response.headers.get("x-rate-limit-reset", "不明")
            raise XApiError(
                f"レート制限に達しました (HTTP 429, reset={reset})。"
                " 無料プランは投稿数の上限が厳しい点に注意してください。"
            )
        if response.status_code >= 400:
            raise XApiError(f"投稿に失敗しました (HTTP {response.status_code}): {response.text}")

        payload = response.json()
        tweet_id = (payload.get("data") or {}).get("id")
        if not tweet_id:
            raise XApiError(f"応答にツイートIDが含まれていません: {payload}")
        return str(tweet_id)


def weighted_length(text: str) -> int:
    """X のカウント方式での文字数（全角・絵文字は2）を返す。"""
    total = 0
    for char in text:
        code = ord(char)
        total += 1 if any(low <= code <= high for low, high in _SINGLE_WEIGHT_RANGES) else 2
    return total


def validate_length(text: str) -> None:
    """空文字と X の上限超過を弾く。"""
    if not text.strip():
        raise XApiError("投稿本文が空です")
    length = weighted_length(text)
    if length > X_MAX_WEIGHTED_LENGTH:
        raise XApiError(
            f"本文が X の上限を超えています ({length}/{X_MAX_WEIGHTED_LENGTH} 相当)"
        )
