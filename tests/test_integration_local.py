"""ローカルのモックサーバに対する結合テスト。

外部ネットワークには出ずに、以下を「実際の HTTP 往復」で検証する:

* XClient が組み立てるリクエスト（メソッド / URL / JSON ボディ / ヘッダ）
* OAuth 1.0a の署名が、同じ鍵で検証側から再計算した署名と一致すること
* X API のエラー応答（401 / 403 / 429）を適切なメッセージに変換すること
* Anthropic SDK に渡している生成リクエストが、実際にどんな JSON になるか

X 本番エンドポイントには到達しないため「投稿できること」そのものは保証しないが、
本番との差分は「宛先ホストと資格情報が本物かどうか」だけになる。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx2 as httpx
import pytest

import anthropic
from x_autopost import x_client as x_client_module
from x_autopost.config import Constraints, GenerationConfig
from x_autopost.generator import PostGenerator
from x_autopost.x_client import XApiError, XClient, XCredentials

CREDS = XCredentials(
    api_key="consumer-key",
    api_secret="consumer-secret",
    access_token="access-token",
    access_token_secret="access-token-secret",
)


class _Handler(BaseHTTPRequestHandler):
    """X API v2 の POST /2/tweets を模したハンドラ。"""

    captured: dict = {}
    status = 201
    body = {"data": {"id": "1890123456789", "text": "ok"}}

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler の規約)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        _Handler.captured = {
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "authorization": self.headers.get("Authorization"),
            "body": raw.decode("utf-8"),
            "url": f"http://{self.headers.get('Host')}{self.path}",
        }
        payload = json.dumps(_Handler.body).encode("utf-8")
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if _Handler.status == 429:
            self.send_header("x-rate-limit-reset", "1900000000")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        _Handler.captured = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": "",
            "url": f"http://{self.headers.get('Host')}{self.path}",
        }
        payload = json.dumps(
            {"data": {"id": "7", "name": "テスト", "username": "test_user"}}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # テスト出力を汚さない
        pass


@pytest.fixture
def mock_x_api(monkeypatch):
    """モック X API を起動し、XClient の宛先をそこへ向ける。"""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/2/tweets"
    monkeypatch.setattr(x_client_module, "TWEETS_ENDPOINT", endpoint)

    _Handler.captured = {}
    _Handler.status = 201
    _Handler.body = {"data": {"id": "1890123456789", "text": "ok"}}
    try:
        yield _Handler
    finally:
        server.shutdown()
        server.server_close()


def parse_oauth_header(header: str) -> dict[str, str]:
    """Authorization: OAuth ... をパースし、値をパーセントデコードして返す。"""
    assert header.startswith("OAuth ")
    params = {}
    for pair in header[len("OAuth ") :].split(", "):
        key, _, value = pair.partition("=")
        params[key.strip()] = urllib.parse.unquote(value.strip().strip('"'))
    return params


def _quote(value: object) -> str:
    return urllib.parse.quote(str(value), safe="~")


def recompute_signature(
    method: str, url: str, oauth_params: dict[str, str], creds: XCredentials
) -> str:
    """RFC 5849 §3.4 に従って HMAC-SHA1 署名を独立に計算し直す。"""
    normalized = "&".join(
        f"{_quote(key)}={_quote(oauth_params[key])}" for key in sorted(oauth_params)
    )
    base_string = "&".join([method, _quote(url), _quote(normalized)])
    signing_key = f"{_quote(creds.api_secret)}&{_quote(creds.access_token_secret)}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_real_http_roundtrip_returns_tweet_id(mock_x_api):
    tweet_id = XClient(CREDS).post_tweet("結合テストからの投稿")
    assert tweet_id == "1890123456789"

    captured = mock_x_api.captured
    assert captured["path"] == "/2/tweets"
    assert captured["content_type"] == "application/json"
    assert json.loads(captured["body"]) == {"text": "結合テストからの投稿"}


def test_oauth1_signature_is_verifiable_by_the_receiver(mock_x_api):
    """受信側で RFC 5849 に従って署名を計算し直し、一致することを確かめる。"""
    XClient(CREDS).post_tweet("署名検証用の投稿")

    captured = mock_x_api.captured
    params = parse_oauth_header(captured["authorization"])

    # X API v2 が要求する OAuth 1.0a のパラメータが揃っているか
    assert params["oauth_consumer_key"] == "consumer-key"
    assert params["oauth_token"] == "access-token"
    assert params["oauth_signature_method"] == "HMAC-SHA1"
    assert params["oauth_version"] == "1.0"
    assert params["oauth_nonce"] and params["oauth_timestamp"]

    signature = params.pop("oauth_signature")
    expected = recompute_signature("POST", captured["url"], params, CREDS)

    assert signature == expected, "OAuth 署名が受信側の再計算と一致しない"


def test_signature_verification_rejects_a_wrong_secret(mock_x_api):
    """検証手順そのものが機能していること（鍵が違えば落ちる）を確認する。"""
    XClient(CREDS).post_tweet("署名検証の妥当性確認")

    captured = mock_x_api.captured
    params = parse_oauth_header(captured["authorization"])
    signature = params.pop("oauth_signature")

    wrong = XCredentials(
        api_key=CREDS.api_key,
        api_secret="wrong-secret",
        access_token=CREDS.access_token,
        access_token_secret=CREDS.access_token_secret,
    )
    assert signature != recompute_signature("POST", captured["url"], params, wrong)


def test_verify_credentials_over_real_http(mock_x_api, monkeypatch):
    """GET /2/users/me も同じ署名手順で通ることを確認する。"""
    base = x_client_module.TWEETS_ENDPOINT.rsplit("/2/", 1)[0]
    monkeypatch.setattr(x_client_module, "USERS_ME_ENDPOINT", f"{base}/2/users/me")

    user = XClient(CREDS).verify_credentials()
    assert user["username"] == "test_user"

    captured = mock_x_api.captured
    assert captured["path"] == "/2/users/me"
    params = parse_oauth_header(captured["authorization"])
    signature = params.pop("oauth_signature")
    assert signature == recompute_signature("GET", captured["url"], params, CREDS)


@pytest.mark.parametrize(
    ("status", "pattern"),
    [
        (401, "HTTP 401"),
        (403, "HTTP 403"),  # Read only トークンで起きる典型的な失敗
        (429, "レート制限"),
    ],
)
def test_error_statuses_are_translated(mock_x_api, status, pattern):
    mock_x_api.status = status
    mock_x_api.body = {"title": "error"}
    with pytest.raises(XApiError, match=pattern):
        XClient(CREDS).post_tweet("エラー応答の確認")


def test_anthropic_request_payload(monkeypatch):
    """Anthropic SDK が実際に送信する JSON ボディを確認する。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["betas"] = request.headers.get("anthropic-beta")
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "生成された投稿文"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

    client = anthropic.Anthropic(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    config = GenerationConfig(
        model="claude-opus-5",
        effort="medium",
        persona="テスト用の中の人",
        themes=["テーマA"],
        constraints=Constraints(language="ja", max_chars=130, hashtags=0, forbid=["URLを含めない"]),
        recent_posts_context=2,
    )

    text = PostGenerator(config, client=client).generate("テーマA", ["過去の投稿"])
    assert text == "生成された投稿文"

    assert "/v1/messages" in captured["url"]
    body = captured["body"]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in captured["betas"]
    assert "130 文字以内" in body["system"]
    assert "過去の投稿" in body["messages"][0]["content"]


def test_full_pipeline_generate_sign_post_and_record(mock_x_api, tmp_path, monkeypatch, capsys):
    """生成 → OAuth 署名付き HTTP 投稿 → 履歴記録 までを通しで実行する。

    Anthropic は HTTP トランスポート層だけ差し替え（SDK の直列化は本物）、
    X はローカルのモックサーバ。CLI からの経路は本番と同一。
    """
    from x_autopost import cli

    (tmp_path / "config.yaml").write_text(
        """
generation:
  model: claude-opus-5
  effort: low
  persona: "テスト用の中の人"
  themes: ["テーマA"]
  constraints:
    language: ja
    max_chars: 130
    hashtags: 0
    forbid: []
  recent_posts_context: 5
posting:
  duplicate_similarity_threshold: 0.8
  max_generation_attempts: 3
history_path: history.json
""",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "通しテストで生成された投稿文です"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

    real_anthropic_cls = anthropic.Anthropic
    monkeypatch.setattr(
        anthropic,
        "Anthropic",
        lambda *a, **kw: real_anthropic_cls(
            api_key="test-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
    )
    for name, value in {
        "X_API_KEY": CREDS.api_key,
        "X_API_SECRET": CREDS.api_secret,
        "X_ACCESS_TOKEN": CREDS.access_token,
        "X_ACCESS_TOKEN_SECRET": CREDS.access_token_secret,
    }.items():
        monkeypatch.setenv(name, value)

    exit_code = cli.main(["--config", str(tmp_path / "config.yaml"), "post"])
    assert exit_code == cli.EXIT_OK

    # X に届いた内容が生成文そのものであること
    captured = mock_x_api.captured
    assert json.loads(captured["body"]) == {"text": "通しテストで生成された投稿文です"}

    # その通信の署名が正しいこと
    params = parse_oauth_header(captured["authorization"])
    signature = params.pop("oauth_signature")
    assert signature == recompute_signature("POST", captured["url"], params, CREDS)

    # 履歴が残り、ツイートURLが表示されること
    records = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["text"] == "通しテストで生成された投稿文です"
    assert records[0]["tweet_id"] == "1890123456789"
    assert "1890123456789" in capsys.readouterr().out
