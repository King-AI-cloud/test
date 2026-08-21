"""config.yaml の読み込みと検証。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """設定ファイルが不正なときに送出する。"""


@dataclass(frozen=True)
class Constraints:
    language: str = "ja"
    max_chars: int = 130
    hashtags: int = 0
    forbid: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GenerationConfig:
    model: str = "claude-opus-5"
    effort: str = "medium"
    persona: str = ""
    themes: list[str] = field(default_factory=list)
    constraints: Constraints = field(default_factory=Constraints)
    recent_posts_context: int = 20


@dataclass(frozen=True)
class PostingConfig:
    duplicate_similarity_threshold: float = 0.8
    max_generation_attempts: int = 3


@dataclass(frozen=True)
class Config:
    generation: GenerationConfig
    posting: PostingConfig
    history_path: Path

    @property
    def max_chars(self) -> int:
        return self.generation.constraints.max_chars


_VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def load_config(path: str | os.PathLike[str]) -> Config:
    """YAML を読み、Config を返す。不正な値は ConfigError。"""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"設定ファイルが見つかりません: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"設定ファイルの形式が不正です: {config_path}")

    return _build(raw, base_dir=config_path.parent)


def _build(raw: dict, base_dir: Path) -> Config:
    gen_raw = raw.get("generation") or {}
    if not isinstance(gen_raw, dict):
        raise ConfigError("generation はマッピングである必要があります")

    themes = gen_raw.get("themes") or []
    if not isinstance(themes, list) or not all(isinstance(t, str) for t in themes):
        raise ConfigError("generation.themes は文字列のリストである必要があります")
    if not themes:
        raise ConfigError("generation.themes に最低1件のテーマが必要です")

    effort = str(gen_raw.get("effort", "medium"))
    if effort not in _VALID_EFFORTS:
        raise ConfigError(
            f"generation.effort が不正です: {effort!r} "
            f"(有効値: {', '.join(sorted(_VALID_EFFORTS))})"
        )

    con_raw = gen_raw.get("constraints") or {}
    if not isinstance(con_raw, dict):
        raise ConfigError("generation.constraints はマッピングである必要があります")

    max_chars = _positive_int(con_raw.get("max_chars", 130), "generation.constraints.max_chars")
    if max_chars > 280:
        raise ConfigError("generation.constraints.max_chars は 280 以下にしてください")

    constraints = Constraints(
        language=str(con_raw.get("language", "ja")),
        max_chars=max_chars,
        hashtags=_non_negative_int(con_raw.get("hashtags", 0), "generation.constraints.hashtags"),
        forbid=[str(x) for x in (con_raw.get("forbid") or [])],
    )

    generation = GenerationConfig(
        model=str(gen_raw.get("model", "claude-opus-5")),
        effort=effort,
        persona=str(gen_raw.get("persona", "")).strip(),
        themes=themes,
        constraints=constraints,
        recent_posts_context=_non_negative_int(
            gen_raw.get("recent_posts_context", 20), "generation.recent_posts_context"
        ),
    )

    post_raw = raw.get("posting") or {}
    if not isinstance(post_raw, dict):
        raise ConfigError("posting はマッピングである必要があります")

    threshold = float(post_raw.get("duplicate_similarity_threshold", 0.8))
    if not 0.0 <= threshold <= 1.0:
        raise ConfigError("posting.duplicate_similarity_threshold は 0.0〜1.0 の範囲です")

    posting = PostingConfig(
        duplicate_similarity_threshold=threshold,
        max_generation_attempts=_positive_int(
            post_raw.get("max_generation_attempts", 3), "posting.max_generation_attempts"
        ),
    )

    history_path = Path(str(raw.get("history_path", "state/history.json")))
    if not history_path.is_absolute():
        history_path = base_dir / history_path

    return Config(generation=generation, posting=posting, history_path=history_path)


def _positive_int(value: object, name: str) -> int:
    number = _non_negative_int(value, name)
    if number == 0:
        raise ConfigError(f"{name} は1以上である必要があります")
    return number


def _non_negative_int(value: object, name: str) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigError(f"{name} は整数である必要があります: {value!r}") from None
    if number < 0:
        raise ConfigError(f"{name} は0以上である必要があります")
    return number
