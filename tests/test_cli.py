import json

import pytest

from x_autopost import cli
from x_autopost.generator import GenerationError
from x_autopost.x_client import XApiError

CONFIG = """
generation:
  model: claude-opus-5
  effort: low
  persona: "テスト"
  themes: ["テーマA"]
  constraints:
    language: ja
    max_chars: 20
    hashtags: 0
    forbid: []
  recent_posts_context: 5
posting:
  duplicate_similarity_threshold: 0.8
  max_generation_attempts: 2
history_path: history.json
"""


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


class FakeGenerator:
    """PostGenerator の差し替え。texts を順に返す。"""

    instances = []

    def __init__(self, config, client=None, rng=None):
        self.config = config
        self.calls = []
        FakeGenerator.instances.append(self)

    def pick_theme(self):
        return "テーマA"

    def generate(self, theme, recent_posts=None):
        self.calls.append((theme, list(recent_posts or [])))
        return FakeGenerator.texts.pop(0)


class FakeXClient:
    instances = []
    tweet_id = "999"
    error = None

    def __init__(self, credentials, session=None, timeout=30.0):
        self.posted = []
        FakeXClient.instances.append(self)

    def post_tweet(self, text):
        if FakeXClient.error:
            raise FakeXClient.error
        self.posted.append(text)
        return FakeXClient.tweet_id


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    FakeGenerator.instances = []
    FakeGenerator.texts = []
    FakeXClient.instances = []
    FakeXClient.tweet_id = "999"
    FakeXClient.error = None
    monkeypatch.setattr(cli, "PostGenerator", FakeGenerator)
    monkeypatch.setattr(cli, "XClient", FakeXClient)
    monkeypatch.setattr(cli.XCredentials, "from_env", classmethod(lambda cls, env=None: object()))
    return None


def run(workspace, *args):
    return cli.main(["--config", str(workspace / "config.yaml"), *args])


def read_history(workspace):
    path = workspace / "history.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def test_dry_run_does_not_post_or_record(workspace, capsys):
    FakeGenerator.texts = ["短い投稿文"]
    assert run(workspace, "post", "--dry-run") == cli.EXIT_OK
    assert "短い投稿文" in capsys.readouterr().out
    assert FakeXClient.instances == []
    assert read_history(workspace) == []


def test_post_writes_history(workspace, capsys):
    FakeGenerator.texts = ["短い投稿文"]
    assert run(workspace, "post") == cli.EXIT_OK

    assert FakeXClient.instances[0].posted == ["短い投稿文"]
    records = read_history(workspace)
    assert len(records) == 1
    assert records[0]["text"] == "短い投稿文"
    assert records[0]["tweet_id"] == "999"
    assert records[0]["theme"] == "テーマA"
    assert "999" in capsys.readouterr().out


def test_regenerates_when_over_max_chars(workspace):
    FakeGenerator.texts = ["あ" * 30, "収まる短い文"]
    assert run(workspace, "post") == cli.EXIT_OK
    assert FakeXClient.instances[0].posted == ["収まる短い文"]


def test_gives_up_after_max_attempts(workspace, capsys):
    FakeGenerator.texts = ["あ" * 30, "い" * 30]
    assert run(workspace, "post") == cli.EXIT_ERROR
    assert "生成エラー" in capsys.readouterr().err


def test_duplicate_is_skipped(workspace, capsys):
    FakeGenerator.texts = ["同じ内容の投稿"]
    assert run(workspace, "post") == cli.EXIT_OK

    FakeGenerator.texts = ["同じ内容の投稿", "同じ内容の投稿"]
    assert run(workspace, "post") == cli.EXIT_SKIPPED  # 再生成しても重複なら見送り
    assert "重複" in capsys.readouterr().err
    assert len(read_history(workspace)) == 1


def test_allow_duplicate_posts_anyway(workspace):
    FakeGenerator.texts = ["同じ内容の投稿"]
    run(workspace, "post")
    FakeGenerator.texts = ["同じ内容の投稿"]
    assert run(workspace, "post", "--allow-duplicate") == cli.EXIT_OK
    assert len(read_history(workspace)) == 2


def test_text_flag_duplicate_is_skipped(workspace, capsys):
    run(workspace, "post", "--text", "手書きの投稿")
    assert run(workspace, "post", "--text", "手書きの投稿") == cli.EXIT_SKIPPED
    assert "重複" in capsys.readouterr().err


def test_text_flag_skips_generation(workspace):
    assert run(workspace, "post", "--text", "手書きの投稿") == cli.EXIT_OK
    assert FakeGenerator.instances == []
    assert FakeXClient.instances[0].posted == ["手書きの投稿"]


def test_recent_posts_are_passed_to_generator(workspace):
    FakeGenerator.texts = ["一件目の投稿"]
    run(workspace, "post")
    FakeGenerator.texts = ["まったく別の話題"]
    run(workspace, "post")

    assert FakeGenerator.instances[1].calls[0][1] == ["一件目の投稿"]


def test_post_error_returns_error_exit(workspace, capsys):
    FakeGenerator.texts = ["短い投稿文"]
    FakeXClient.error = XApiError("レート制限に達しました")
    assert run(workspace, "post") == cli.EXIT_ERROR
    assert "投稿エラー" in capsys.readouterr().err
    assert read_history(workspace) == []


def test_generation_error_returns_error_exit(workspace, monkeypatch, capsys):
    def boom(self, theme, recent_posts=None):
        raise GenerationError("拒否されました")

    monkeypatch.setattr(FakeGenerator, "generate", boom)
    assert run(workspace, "post") == cli.EXIT_ERROR
    assert "生成エラー" in capsys.readouterr().err


def test_history_command(workspace, capsys):
    FakeGenerator.texts = ["履歴に残る投稿"]
    run(workspace, "post")
    assert run(workspace, "history") == cli.EXIT_OK
    assert "履歴に残る投稿" in capsys.readouterr().out


def test_history_command_when_empty(workspace, capsys):
    assert run(workspace, "history") == cli.EXIT_OK
    assert "まだありません" in capsys.readouterr().out


def test_bad_config_path(capsys):
    assert cli.main(["--config", "/nonexistent/config.yaml", "history"]) == cli.EXIT_ERROR
    assert "設定エラー" in capsys.readouterr().err
