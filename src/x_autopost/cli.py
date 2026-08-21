"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import anthropic

from .config import Config, ConfigError, load_config
from .generator import GenerationError, PostGenerator
from .history import History, PostRecord
from .x_client import (
    X_MAX_WEIGHTED_LENGTH,
    XApiError,
    XClient,
    XCredentials,
    weighted_length,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SKIPPED = 2

DEFAULT_CONFIG = "config.yaml"


class DuplicatePostError(Exception):
    """生成した文章が直近の投稿と重複していたときに送出する。"""


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.command == "post":
        return _cmd_post(args, config)
    if args.command == "history":
        return _cmd_history(args, config)
    parser.print_help()
    return EXIT_ERROR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="x-autopost", description="Claude で生成した文章を X に自動投稿する"
    )
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG, help="設定ファイルのパス (既定: config.yaml)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    post = sub.add_parser("post", help="投稿文を生成して X に投稿する")
    post.add_argument(
        "--dry-run",
        action="store_true",
        help="生成だけ行い、投稿も履歴への記録もしない",
    )
    post.add_argument("--text", help="生成せず、指定した本文をそのまま投稿する")
    post.add_argument("--theme", help="テーマをランダムに選ばずに指定する")
    post.add_argument(
        "--allow-duplicate", action="store_true", help="重複チェックに引っかかっても投稿する"
    )

    history = sub.add_parser("history", help="投稿履歴を表示する")
    history.add_argument("-n", "--limit", type=int, default=10, help="表示件数 (既定: 10)")

    return parser


def _cmd_post(args: argparse.Namespace, config: Config) -> int:
    history = History.load(config.history_path)

    try:
        if args.text:
            text, theme = args.text, args.theme
            _check_duplicate(text, config, history, args.allow_duplicate)
        else:
            text, theme = _generate_with_retries(args, config, history)
    except DuplicatePostError as exc:
        print(f"重複のため投稿を見送りました: {exc}", file=sys.stderr)
        return EXIT_SKIPPED
    except (GenerationError, anthropic.APIError) as exc:
        print(f"生成エラー: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"--- 投稿文 ({len(text)} 文字 / X換算 {weighted_length(text)}) ---")
    print(text)
    print("---")

    if args.dry_run:
        print("[dry-run] 投稿はしていません。")
        return EXIT_OK

    try:
        client = XClient(XCredentials.from_env())
        tweet_id = client.post_tweet(text)
    except XApiError as exc:
        print(f"投稿エラー: {exc}", file=sys.stderr)
        return EXIT_ERROR

    history.append(
        PostRecord(
            text=text,
            posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            tweet_id=tweet_id,
            theme=theme,
        )
    )
    history.save()
    print(f"投稿しました: https://x.com/i/web/status/{tweet_id}")
    return EXIT_OK


def _check_duplicate(
    text: str, config: Config, history: History, allow_duplicate: bool
) -> None:
    """直近の投稿と似すぎていれば DuplicatePostError を送出する。"""
    if allow_duplicate:
        return
    score, similar = history.most_similar(text)
    if score >= config.posting.duplicate_similarity_threshold:
        raise DuplicatePostError(f"類似度 {score:.2f} / 既存の投稿: {similar}")


def _generate_with_retries(
    args: argparse.Namespace, config: Config, history: History
) -> tuple[str, str]:
    """条件（文字数・重複）を満たす文章が出るまで生成をやり直す。"""
    generator = PostGenerator(config.generation)
    recent = history.recent_texts(config.generation.recent_posts_context)
    attempts = config.posting.max_generation_attempts
    last_reason = "不明"
    last_was_duplicate = False

    for attempt in range(1, attempts + 1):
        theme = args.theme or generator.pick_theme()
        text = generator.generate(theme, recent)

        if len(text) > config.max_chars:
            last_reason = f"{len(text)} 文字で上限 {config.max_chars} 超過"
            last_was_duplicate = False
        elif weighted_length(text) > X_MAX_WEIGHTED_LENGTH:
            last_reason = f"X の {X_MAX_WEIGHTED_LENGTH} 文字換算を超過"
            last_was_duplicate = False
        else:
            try:
                _check_duplicate(text, config, history, args.allow_duplicate)
            except DuplicatePostError as exc:
                last_reason, last_was_duplicate = str(exc), True
            else:
                return text, theme

        print(f"[{attempt}/{attempts}] 再生成します（理由: {last_reason}）", file=sys.stderr)

    if last_was_duplicate:
        raise DuplicatePostError(f"{attempts} 回生成しましたがすべて重複でした（{last_reason}）")
    raise GenerationError(
        f"{attempts} 回試しましたが条件を満たす文章を生成できませんでした（{last_reason}）"
    )


def _cmd_history(args: argparse.Namespace, config: Config) -> int:
    history = History.load(config.history_path)
    records = list(reversed(history.records))[: max(args.limit, 0)]
    if not records:
        print("投稿履歴はまだありません。")
        return EXIT_OK
    for record in records:
        print(f"{record.posted_at}  [{record.tweet_id}]  {record.text}")
    return EXIT_OK
