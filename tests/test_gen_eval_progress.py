"""生成評測的斷點續跑：每題落地、重跑時略過已完成的題目"""
import json
from unittest.mock import patch

from models.shelter import Shelter
from evals.run_gen_eval import _append_line, load_progress, progress_path_for, run

SIGNATURE = {"model": "qwen2.5:7b", "num_ctx": 8192}
SHELTERS = [
    Shelter("[YILAN] 宜蘭國小", 500, 24.7, 121.7, 0, "宜蘭縣宜蘭市崇聖街2號"),
    Shelter("[HUALIEN] 中正國小", 400, 23.9, 121.6, 0, "花蓮縣花蓮市中正路210號"),
]
CASES = [
    {"query": "宜蘭國小", "relevant": ["[YILAN] 宜蘭國小"], "category": "exact_name"},
    {"query": "中正國小", "relevant": ["[HUALIEN] 中正國小"], "category": "exact_name"},
]


class FakeChatService:
    """記錄實際生成了幾次，用來確認略過的題目沒有再打一次模型"""
    generated = []

    def __init__(self, vector_store=None, repo=None):
        pass

    def build_context(self, query):
        return f"- {query}是避難收容場所，容量 500 人。", None

    def build_prompt(self, query, context):
        return f"{context}\n{query}"

    def generate(self, prompt):
        FakeChatService.generated.append(prompt)
        return {"response": "宜蘭國小容量 500 人。", "done_reason": "stop", "eval_count": 12}


def test_progress_path_is_derived_from_output():
    assert progress_path_for("/tmp/x.json") == "/tmp/x.json.progress.jsonl"


def test_load_progress_without_file_or_with_other_signature(tmp_path):
    path = str(tmp_path / "r.json.progress.jsonl")
    assert load_progress(path, SIGNATURE) == {}

    with open(path, "w", encoding="utf-8") as f:
        _append_line(f, {"signature": {"model": "llama3.2:3b"}})
        _append_line(f, {"query": "宜蘭國小", "scores": {}})
    # 換了模型就不沿用舊結果
    assert load_progress(path, SIGNATURE) == {}
    assert set(load_progress(path, {"model": "llama3.2:3b"})) == {"宜蘭國小"}


def test_load_progress_drops_a_half_written_last_line(tmp_path):
    path = str(tmp_path / "r.json.progress.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        _append_line(f, {"signature": SIGNATURE})
        _append_line(f, {"query": "宜蘭國小", "reply": "ok"})
        f.write('{"query": "中正國小", "rep')  # 斷電斷在一半
    done = load_progress(path, SIGNATURE)
    assert list(done) == ["宜蘭國小"]
    assert done["宜蘭國小"]["reply"] == "ok"


def test_run_appends_each_answer_and_resumes_where_it_stopped(tmp_path):
    path = str(tmp_path / "r.json.progress.jsonl")
    FakeChatService.generated = []

    with patch("services.chat_service.ChatService", FakeChatService):
        # 第一次：只跑第一題就當作被中止
        with open(path, "w", encoding="utf-8") as f:
            _append_line(f, {"signature": SIGNATURE})
            rows = run(CASES[:1], store=None, shelters=SHELTERS, progress_handle=f)
        assert len(rows) == 1
        assert len(FakeChatService.generated) == 1

        # 進度檔裡已經有第一題的完整結果
        done = load_progress(path, SIGNATURE)
        assert list(done) == ["宜蘭國小"]
        assert done["宜蘭國小"]["scores"]["answer_recall"] == 1.0

        # 第二次：接著跑完整組，第一題直接沿用不再打模型
        with open(path, "a", encoding="utf-8") as f:
            rows = run(CASES, store=None, shelters=SHELTERS, progress_handle=f, done=done)

    assert [r["query"] for r in rows] == ["宜蘭國小", "中正國小"]
    assert len(FakeChatService.generated) == 2  # 只多生成了第二題
    assert len(load_progress(path, SIGNATURE)) == 2


def test_progress_lines_are_valid_json_rows(tmp_path):
    path = str(tmp_path / "r.json.progress.jsonl")
    FakeChatService.generated = []
    with patch("services.chat_service.ChatService", FakeChatService):
        with open(path, "w", encoding="utf-8") as f:
            _append_line(f, {"signature": SIGNATURE})
            run(CASES, store=None, shelters=SHELTERS, progress_handle=f)
    lines = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    assert lines[0] == {"signature": SIGNATURE}
    assert [row["query"] for row in lines[1:]] == ["宜蘭國小", "中正國小"]
    assert all("scores" in row and "reply" in row for row in lines[1:])
