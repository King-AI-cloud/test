# X 自動投稿ツール

Claude API で投稿文を生成し、X (旧 Twitter) API v2 に自動投稿する CLI ツールです。
GitHub Actions の cron で定期実行することを想定しています。

- 生成: Claude (`claude-opus-5`) — 人格・テーマ・文字数を `config.yaml` で設定
- 投稿: X API v2 `POST /2/tweets`（OAuth 1.0a ユーザーコンテキスト）
- 重複防止: 投稿履歴 (`state/history.json`) と類似度を比較し、似すぎていたら再生成・見送り

---

## 1. 必要なもの

| 用途 | 取得先 | 備考 |
| --- | --- | --- |
| Anthropic API キー | [console.anthropic.com](https://console.anthropic.com/) | 従量課金 |
| X の API キー4種 | [developer.x.com](https://developer.x.com/) の Developer Portal | **Free プランでも投稿は可能**（後述の制限あり） |

### X 側の設定で間違えやすい点

1. Developer Portal で App を作る
2. **User authentication settings** を有効にし、**App permissions を "Read and write"** にする
   （Read only のままだと投稿時に HTTP 403 になります）
3. パーミッションを変更した **後で** Access Token / Secret を再発行する
   （権限変更前に発行したトークンは Read only のままです）
4. 以下4つを控える
   - API Key (`X_API_KEY`)
   - API Key Secret (`X_API_SECRET`)
   - Access Token (`X_ACCESS_TOKEN`)
   - Access Token Secret (`X_ACCESS_TOKEN_SECRET`)

### 投稿数の上限（重要）

X API の Free プランは投稿数の上限が非常に厳しく（執筆時点でユーザー単位 1日17件 / 月500件程度）、
プラン内容も変わります。**必ず最新の料金ページで現在の上限を確認してから cron の頻度を決めてください。**
このリポジトリの既定は 1日2回です。

---

## 2. ローカルでの動作確認

```bash
pip install -r requirements-dev.txt

export ANTHROPIC_API_KEY=sk-ant-...
export X_API_KEY=...
export X_API_SECRET=...
export X_ACCESS_TOKEN=...
export X_ACCESS_TOKEN_SECRET=...

# 生成だけして中身を確認（投稿しない・履歴も残さない）
PYTHONPATH=src python -m x_autopost post --dry-run

# 実際に投稿する
PYTHONPATH=src python -m x_autopost post

# 投稿履歴を見る
PYTHONPATH=src python -m x_autopost history -n 20
```

`pip install -e .` すると `x-autopost` コマンドとしても使えます（`PYTHONPATH` は不要になります）。

### コマンド一覧

```
x-autopost [-c CONFIG] post [--dry-run] [--text TEXT] [--theme THEME] [--allow-duplicate]
x-autopost [-c CONFIG] history [-n LIMIT]
```

| オプション | 説明 |
| --- | --- |
| `--dry-run` | 生成結果を表示するだけ。投稿も履歴記録もしない |
| `--text` | 生成せず、指定した本文をそのまま投稿する |
| `--theme` | テーマをランダムに選ばず指定する |
| `--allow-duplicate` | 重複チェックを無視して投稿する |

終了コード: `0` 成功 / `1` エラー / `2` 重複のため見送り

---

## 3. GitHub Actions で自動化する

1. リポジトリの **Settings → Secrets and variables → Actions** に5つの Secret を登録

   `ANTHROPIC_API_KEY` / `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET`

2. **Settings → Actions → General → Workflow permissions** で
   **Read and write permissions** を有効にする（投稿履歴のコミットに必要）

3. `.github/workflows/auto-post.yml` の `cron` を好みの時間に変更する
   （**UTC 指定**です。JST から9時間引いてください）

   ```yaml
   schedule:
     - cron: "0 0 * * *"   # 09:00 JST
     - cron: "0 10 * * *"  # 19:00 JST
   ```

4. Actions タブから **X 自動投稿 → Run workflow** で手動実行して動作確認
   （`dry_run` にチェックを入れれば投稿されません）

### 仕組み

- 実行のたびに `state/history.json` に投稿を追記し、ワークフローがそれをコミットして戻します。
  これにより次回実行時に「直近の投稿」を Claude に渡して内容の重複を避けられます。
- 生成文が重複していた場合は終了コード 2 で見送り、ワークフローは失敗扱いにしません。

### 注意

- GitHub Actions の `schedule` は**指定時刻ちょうどには動きません**（数分〜十数分遅れることがあります）。
  分単位の正確さが必要なら VPS の cron や systemd timer を使ってください。
- 公開リポジトリで放置された scheduled workflow は、60日間コミットがないと自動停止します。

---

## 4. 投稿内容のカスタマイズ

`config.yaml` を編集します。秘密情報は書きません（すべて環境変数）。

```yaml
generation:
  model: claude-opus-5      # 使用モデル
  effort: medium            # low / medium / high / xhigh / max
  persona: |                # アカウントの人格・語り口
    ...
  themes:                   # 毎回この中から1つランダムに選ばれる
    - "..."
  constraints:
    language: ja
    max_chars: 130          # 生成文の上限（X の上限280は全角換算なので安全側に）
    hashtags: 0
    forbid:                 # 禁止事項（そのまま system prompt に入る）
      - "..."
  recent_posts_context: 20  # 直近何件を「被らせないで」と渡すか

posting:
  duplicate_similarity_threshold: 0.8  # これ以上似ていたら重複とみなす
  max_generation_attempts: 3           # 条件を満たすまでの再生成回数

history_path: state/history.json
```

---

## 5. 構成

```
src/x_autopost/
  config.py      config.yaml の読み込みと検証
  generator.py   Claude API で投稿文を生成
  x_client.py    X API v2 への投稿・文字数計算
  history.py     投稿履歴の保存と類似度による重複検出
  cli.py         コマンドライン
.github/workflows/
  auto-post.yml  cron で定期投稿
  ci.yml         テスト
tests/           pytest
```

テスト実行:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

外部 API は呼ばずにモックで検証しているため、API キーなしで実行できます。
