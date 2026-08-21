"""投稿履歴の永続化と重複検出。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class PostRecord:
    """1件の投稿記録。"""

    text: str
    posted_at: str
    tweet_id: str | None = None
    theme: str | None = None


class History:
    """`state/history.json` に投稿履歴を貯める。

    GitHub Actions で運用する場合は、ワークフロー側でこのファイルを
    コミットして戻すことで実行間の履歴が保持される。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.records: list[PostRecord] = []

    @classmethod
    def load(cls, path: str | Path) -> "History":
        history = cls(path)
        if history.path.is_file():
            raw = json.loads(history.path.read_text(encoding="utf-8") or "[]")
            history.records = [
                PostRecord(
                    text=str(item.get("text", "")),
                    posted_at=str(item.get("posted_at", "")),
                    tweet_id=item.get("tweet_id"),
                    theme=item.get("theme"),
                )
                for item in raw
            ]
        return history

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(record) for record in self.records]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def append(self, record: PostRecord) -> None:
        self.records.append(record)

    def recent_texts(self, limit: int) -> list[str]:
        """新しい順に最大 limit 件の本文を返す。"""
        if limit <= 0:
            return []
        return [record.text for record in reversed(self.records)][:limit]

    def most_similar(self, text: str, limit: int = 200) -> tuple[float, str | None]:
        """直近 limit 件の中で最も似ている投稿との類似度と、その本文を返す。"""
        best_score = 0.0
        best_text: str | None = None
        target = normalize(text)
        if not target:
            return 0.0, None
        for past in self.recent_texts(limit):
            score = SequenceMatcher(None, target, normalize(past)).ratio()
            if score > best_score:
                best_score, best_text = score, past
        return best_score, best_text


_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """類似度比較用に表記ゆれをならす（全角半角・空白・大文字小文字）。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE.sub(" ", normalized).strip()
