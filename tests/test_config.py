import pytest

from x_autopost.config import ConfigError, load_config

VALID = """
generation:
  model: claude-opus-5
  effort: low
  persona: "テスト"
  themes: ["テーマA", "テーマB"]
  constraints:
    language: ja
    max_chars: 100
    hashtags: 1
    forbid: ["URLを含めない"]
  recent_posts_context: 5
posting:
  duplicate_similarity_threshold: 0.7
  max_generation_attempts: 2
history_path: state/history.json
"""


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_valid_config(tmp_path):
    config = load_config(write(tmp_path, VALID))
    assert config.generation.model == "claude-opus-5"
    assert config.generation.themes == ["テーマA", "テーマB"]
    assert config.max_chars == 100
    assert config.posting.max_generation_attempts == 2
    # 相対パスは設定ファイルからの相対で解決される
    assert config.history_path == tmp_path / "state" / "history.json"


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="見つかりません"):
        load_config(tmp_path / "nope.yaml")


def test_rejects_empty_themes(tmp_path):
    with pytest.raises(ConfigError, match="themes"):
        load_config(write(tmp_path, VALID.replace('["テーマA", "テーマB"]', "[]")))


def test_rejects_bad_effort(tmp_path):
    with pytest.raises(ConfigError, match="effort"):
        load_config(write(tmp_path, VALID.replace("effort: low", "effort: turbo")))


def test_rejects_max_chars_over_280(tmp_path):
    with pytest.raises(ConfigError, match="280"):
        load_config(write(tmp_path, VALID.replace("max_chars: 100", "max_chars: 500")))


def test_rejects_threshold_out_of_range(tmp_path):
    body = VALID.replace("duplicate_similarity_threshold: 0.7", "duplicate_similarity_threshold: 1.5")
    with pytest.raises(ConfigError, match="0.0"):
        load_config(write(tmp_path, body))


def test_repo_config_is_valid():
    config = load_config("config.yaml")
    assert config.generation.themes
