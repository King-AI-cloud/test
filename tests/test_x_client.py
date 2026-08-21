import pytest
import requests

from x_autopost.x_client import (
    MissingCredentialsError,
    XApiError,
    XClient,
    XCredentials,
    validate_length,
    weighted_length,
)

ENV = {
    "X_API_KEY": "k",
    "X_API_SECRET": "s",
    "X_ACCESS_TOKEN": "t",
    "X_ACCESS_TOKEN_SECRET": "ts",
}


class FakeResponse:
    def __init__(self, status_code=201, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.exc:
            raise self.exc
        return self.response


def make_client(session):
    return XClient(XCredentials.from_env(ENV), session=session)


def test_credentials_from_env_requires_all_vars():
    incomplete = dict(ENV)
    del incomplete["X_ACCESS_TOKEN"]
    with pytest.raises(MissingCredentialsError, match="X_ACCESS_TOKEN"):
        XCredentials.from_env(incomplete)


def test_post_tweet_returns_id():
    session = FakeSession(FakeResponse(payload={"data": {"id": "1890"}}))
    assert make_client(session).post_tweet("こんにちは") == "1890"

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://api.x.com/2/tweets"
    assert kwargs["json"] == {"text": "こんにちは"}
    assert kwargs["auth"] is not None


def test_rate_limit_message():
    session = FakeSession(FakeResponse(status_code=429, headers={"x-rate-limit-reset": "123"}))
    with pytest.raises(XApiError, match="レート制限"):
        make_client(session).post_tweet("本文")


def test_http_error_includes_body():
    session = FakeSession(FakeResponse(status_code=400, text="Bad Request"))
    with pytest.raises(XApiError, match="Bad Request"):
        make_client(session).post_tweet("本文")


def test_403_explains_write_permission():
    session = FakeSession(FakeResponse(status_code=403, text="Forbidden"))
    with pytest.raises(XApiError, match="Read and write"):
        make_client(session).post_tweet("本文")


def test_verify_credentials_returns_user():
    payload = {"data": {"id": "7", "name": "テスト", "username": "test_user"}}
    session = FakeSession(FakeResponse(status_code=200, payload=payload))
    assert make_client(session).verify_credentials()["username"] == "test_user"

    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url == "https://api.x.com/2/users/me"


def test_verify_credentials_rejects_empty_payload():
    session = FakeSession(FakeResponse(status_code=200, payload={"data": {}}))
    with pytest.raises(XApiError, match="ユーザー情報"):
        make_client(session).verify_credentials()


def test_missing_id_in_payload():
    session = FakeSession(FakeResponse(payload={"data": {}}))
    with pytest.raises(XApiError, match="ツイートID"):
        make_client(session).post_tweet("本文")


def test_connection_error_is_wrapped():
    session = FakeSession(exc=requests.ConnectionError("boom"))
    with pytest.raises(XApiError, match="接続に失敗"):
        make_client(session).post_tweet("本文")


def test_weighted_length_counts_cjk_as_two():
    assert weighted_length("abc") == 3
    assert weighted_length("あいう") == 6
    assert weighted_length("") == 0


def test_validate_length_rejects_empty_and_too_long():
    with pytest.raises(XApiError, match="空です"):
        validate_length("   ")
    with pytest.raises(XApiError, match="上限"):
        validate_length("あ" * 141)
    validate_length("あ" * 140)  # ちょうど 280 相当は通る
