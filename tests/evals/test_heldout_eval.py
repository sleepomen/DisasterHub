import pytest

from evals import run_heldout_eval as heldout


def row(verdict, route="rag", by_code="是", author="A"):
    return {"id": "1", "出題者": author, "問題": "q", "路由": route, "不經過模型": by_code, "判定": verdict}


def test_questions_from_an_excel_csv_keep_their_text_and_get_ids(tmp_path):
    path = tmp_path / "q.csv"
    # Excel 存的 CSV 有 BOM、可能有空白列
    path.write_bytes("﻿id,出題者,問題,期待的回答\r\n,A, 花蓮市的國小 ,\r\n,,,\r\n7,B,哪裡可以帶寵物,\r\n".encode())
    questions = heldout.load_questions(str(path))
    assert [(q["id"], q["問題"]) for q in questions] == [("1", "花蓮市的國小"), ("7", "哪裡可以帶寵物")]


def test_a_file_without_a_question_column_is_rejected(tmp_path):
    path = tmp_path / "q.csv"
    path.write_text("id,text\n1,hello\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        heldout.load_questions(str(path))


def test_correct_refusals_count_as_correct_and_partial_answers_do_not():
    summary = heldout.score([row("正確"), row("正確拒答", route="off_topic"), row("部分正確"), row("不該拒答")])
    assert summary["overall"]["correct"] == 2
    assert summary["overall"]["accuracy"] == 0.5
    assert summary["by_route"]["off_topic"]["accuracy"] == 1.0


def test_scores_are_split_by_whether_the_model_wrote_the_answer():
    summary = heldout.score([row("正確"), row("錯誤", by_code="否"), row("正確", by_code="否", author="B")])
    assert summary["by_answer_path"]["程式組的回答"]["accuracy"] == 1.0
    assert summary["by_answer_path"]["模型生成"]["accuracy"] == 0.5
    assert summary["authors"] == 2


def test_an_unknown_verdict_stops_the_scoring():
    with pytest.raises(SystemExit):
        heldout.score([row("對")])


def test_the_answer_sheet_round_trips_through_csv(tmp_path):
    path = tmp_path / "a.csv"
    rows = [{**row("正確"), "系統回答": "第一行\n第二行，有逗號"}]
    heldout.write_csv(str(path), rows, heldout.ANSWER_FIELDS)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert heldout.read_csv(str(path))[0]["系統回答"] == "第一行\n第二行，有逗號"
