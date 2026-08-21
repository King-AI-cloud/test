import random
import types

import pytest

from x_autopost.config import Constraints, GenerationConfig
from x_autopost.generator import (
    GenerationError,
    MissingAnthropicCredentialsError,
    PostGenerator,
)


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn", stop_details=None):
        blocks = []
        if text is not None:
            blocks.append(types.SimpleNamespace(type="thinking", thinking=""))
            blocks.append(types.SimpleNamespace(type="text", text=text))
        self.content = blocks
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class FakeClient:
    """anthropic.Anthropic の client.beta.messages.create だけを模倣する。"""

    def __init__(self, response):
        self.calls = []
        create = self._create
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        self._response = response

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def make_config(**overrides):
    base = dict(
        model="claude-opus-5",
        effort="low",
        persona="テスト用の中の人",
        themes=["テーマA", "テーマB"],
        constraints=Constraints(language="ja", max_chars=100, hashtags=0, forbid=["URLを含めない"]),
        recent_posts_context=2,
    )
    base.update(overrides)
    return GenerationConfig(**base)


def test_generate_returns_text_and_sends_expected_params():
    client = FakeClient(FakeResponse("ログを読む習慣の話"))
    generator = PostGenerator(make_config(), client=client)

    assert generator.generate("テーマA", ["過去の投稿1"]) == "ログを読む習慣の話"

    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert "テーマA" in call["messages"][0]["content"]
    assert "過去の投稿1" in call["messages"][0]["content"]
    assert "100 文字以内" in call["system"]
    assert "URLを含めない" in call["system"]


def test_recent_posts_are_truncated_to_context_limit():
    client = FakeClient(FakeResponse("本文"))
    generator = PostGenerator(make_config(recent_posts_context=2), client=client)

    generator.generate("テーマA", ["p1", "p2", "p3"])

    prompt = client.calls[0]["messages"][0]["content"]
    assert "p1" in prompt and "p2" in prompt
    assert "p3" not in prompt


def test_no_recent_posts_message():
    client = FakeClient(FakeResponse("本文"))
    PostGenerator(make_config(), client=client).generate("テーマA", [])
    assert "直近の投稿はまだありません" in client.calls[0]["messages"][0]["content"]


def test_strips_surrounding_quotes():
    client = FakeClient(FakeResponse('「囲まれた本文」'))
    assert PostGenerator(make_config(), client=client).generate("テーマA") == "囲まれた本文"


def test_refusal_raises():
    response = FakeResponse(
        None, stop_reason="refusal", stop_details=types.SimpleNamespace(category="cyber")
    )
    generator = PostGenerator(make_config(), client=FakeClient(response))
    with pytest.raises(GenerationError, match="cyber"):
        generator.generate("テーマA")


def test_empty_response_raises():
    generator = PostGenerator(make_config(), client=FakeClient(FakeResponse("   ")))
    with pytest.raises(GenerationError, match="テキスト"):
        generator.generate("テーマA")


def test_pick_theme_is_deterministic_with_seeded_rng():
    generator = PostGenerator(make_config(), client=FakeClient(FakeResponse("x")), rng=random.Random(0))
    assert generator.pick_theme() in {"テーマA", "テーマB"}


def test_hashtag_rule_switches_on_count():
    without = PostGenerator(make_config(), client=FakeClient(FakeResponse("x")))._system_prompt()
    assert "ハッシュタグは付けない" in without

    constraints = Constraints(language="ja", max_chars=100, hashtags=2, forbid=[])
    with_tags = PostGenerator(
        make_config(constraints=constraints), client=FakeClient(FakeResponse("x"))
    )._system_prompt()
    assert "2 個だけ付ける" in with_tags


def test_missing_anthropic_credentials_message():
    class ExplodingClient(FakeClient):
        def _create(self, **kwargs):
            raise TypeError("Could not resolve authentication method. Expected one of api_key...")

    generator = PostGenerator(make_config(), client=ExplodingClient(None))
    with pytest.raises(MissingAnthropicCredentialsError, match="ANTHROPIC_API_KEY"):
        generator.generate("テーマA")


def test_unrelated_type_error_propagates():
    class ExplodingClient(FakeClient):
        def _create(self, **kwargs):
            raise TypeError("something else entirely")

    with pytest.raises(TypeError, match="something else"):
        PostGenerator(make_config(), client=ExplodingClient(None)).generate("テーマA")
