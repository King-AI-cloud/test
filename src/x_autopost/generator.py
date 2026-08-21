"""Claude API を使って投稿文を生成する。"""

from __future__ import annotations

import random

import anthropic

from .config import GenerationConfig

# サーバーサイドフォールバック（拒否カテゴリに応じて別モデルへ自動ルーティング）
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_SYSTEM_TEMPLATE = """\
{persona}

あなたの仕事は、指定されたテーマについて X（旧 Twitter）に投稿する短文を1つ書くことです。

必ず守るルール:
- 出力は投稿本文だけ。前置き・後書き・引用符・「投稿案:」のようなラベルは一切付けない。
- 言語: {language}
- 本文は {max_chars} 文字以内（改行も1文字として数える）。
- ハッシュタグは {hashtags} 個。
{hashtag_rule}
- 直近の投稿と話題・言い回しが被らないようにする。
{forbid_rules}
"""

_USER_TEMPLATE = """\
今回のテーマ: {theme}

{recent_block}
このテーマで投稿を1つ書いてください。"""


class GenerationError(Exception):
    """生成に失敗したときに送出する。"""


class MissingAnthropicCredentialsError(GenerationError):
    """Anthropic の認証情報が見つからないときに送出する。"""


class PostGenerator:
    """設定に従って投稿文を1件生成する。"""

    def __init__(
        self,
        config: GenerationConfig,
        client: anthropic.Anthropic | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.client = client or anthropic.Anthropic()
        self.rng = rng or random.Random()

    def pick_theme(self) -> str:
        return self.rng.choice(self.config.themes)

    def generate(self, theme: str, recent_posts: list[str] | None = None) -> str:
        """テーマと直近投稿を渡して本文を生成し、整形済みの文字列を返す。"""
        try:
            response = self.client.beta.messages.create(
                model=self.config.model,
                max_tokens=2000,
                betas=[_FALLBACK_BETA],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": self.config.effort},
                system=self._system_prompt(),
                messages=[
                    {"role": "user", "content": self._user_prompt(theme, recent_posts or [])}
                ],
            )
        except TypeError as exc:
            # SDK は認証情報を解決できないと TypeError を投げる
            if "authentication method" not in str(exc):
                raise
            raise MissingAnthropicCredentialsError(
                "Anthropic の認証情報が見つかりません。環境変数 ANTHROPIC_API_KEY を設定してください。"
            ) from exc

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise GenerationError(f"モデルが生成を拒否しました (category={category})")

        return _clean(_extract_text(response))

    def _system_prompt(self) -> str:
        constraints = self.config.constraints
        hashtag_rule = (
            "- ハッシュタグは付けない。"
            if constraints.hashtags == 0
            else f"- ハッシュタグは本文の末尾にまとめて {constraints.hashtags} 個だけ付ける。"
        )
        forbid_rules = "\n".join(f"- {rule}" for rule in constraints.forbid)
        return _SYSTEM_TEMPLATE.format(
            persona=self.config.persona or "あなたは X アカウントの中の人です。",
            language=constraints.language,
            max_chars=constraints.max_chars,
            hashtags=constraints.hashtags,
            hashtag_rule=hashtag_rule,
            forbid_rules=forbid_rules,
        )

    def _user_prompt(self, theme: str, recent_posts: list[str]) -> str:
        limit = self.config.recent_posts_context
        shown = recent_posts[:limit] if limit > 0 else []
        if shown:
            listed = "\n".join(f"- {text}" for text in shown)
            recent_block = f"直近の投稿（これらと被らないこと）:\n{listed}\n"
        else:
            recent_block = "直近の投稿はまだありません。\n"
        return _USER_TEMPLATE.format(theme=theme, recent_block=recent_block)


def _extract_text(response: object) -> str:
    blocks = getattr(response, "content", []) or []
    parts = [block.text for block in blocks if getattr(block, "type", None) == "text"]
    text = "".join(parts).strip()
    if not text:
        raise GenerationError("モデルの応答にテキストが含まれていませんでした")
    return text


def _clean(text: str) -> str:
    """モデルが付けがちな囲み記号を落とす。"""
    cleaned = text.strip()
    for quote in ('"', "'", "「", "『"):
        closing = {'"': '"', "'": "'", "「": "」", "『": "』"}[quote]
        if cleaned.startswith(quote) and cleaned.endswith(closing) and len(cleaned) > 1:
            cleaned = cleaned[1:-1].strip()
    return cleaned
