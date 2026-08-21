from x_autopost.history import History, PostRecord, normalize


def make(tmp_path):
    return History(tmp_path / "history.json")


def test_roundtrip(tmp_path):
    history = make(tmp_path)
    history.append(PostRecord(text="こんにちは", posted_at="2026-01-01T00:00:00+00:00", tweet_id="1"))
    history.save()

    reloaded = History.load(tmp_path / "history.json")
    assert len(reloaded.records) == 1
    assert reloaded.records[0].text == "こんにちは"
    assert reloaded.records[0].tweet_id == "1"


def test_load_missing_file_is_empty(tmp_path):
    assert History.load(tmp_path / "absent.json").records == []


def test_recent_texts_is_newest_first(tmp_path):
    history = make(tmp_path)
    for i in range(5):
        history.append(PostRecord(text=f"post{i}", posted_at="t"))
    assert history.recent_texts(2) == ["post4", "post3"]
    assert history.recent_texts(0) == []


def test_most_similar_detects_near_duplicate(tmp_path):
    history = make(tmp_path)
    history.append(PostRecord(text="毎朝5分だけログを読むと障害対応が速くなる", posted_at="t"))

    score, similar = history.most_similar("毎朝5分だけログを読むと障害対応が速くなります")
    assert score > 0.8
    assert similar is not None

    score_other, _ = history.most_similar("今日は良い天気ですね")
    assert score_other < 0.5


def test_most_similar_on_empty_history(tmp_path):
    assert make(tmp_path).most_similar("なにか") == (0.0, None)


def test_normalize_folds_width_and_whitespace():
    assert normalize("ＡＢＣ  \n Def") == "abc def"
