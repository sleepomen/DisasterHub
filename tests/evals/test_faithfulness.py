from evals.lib.faithfulness import (
    drop_target,
    integers_in,
    pick_counterfactual,
    score_drop,
    score_swap,
    swap_number,
    target_line_index,
)
from evals.lib.gen_metrics import build_catalog
from models.shelter import Shelter
from services.retrieval.vector_store import ALIAS_PATTERN, Hit, VectorStore

SHELTERS = [
    Shelter("[YILAN] 宜蘭國小", 500, 24.7, 121.7, 0, "宜蘭縣宜蘭市崇聖街2號"),
    Shelter("[YILAN] 宜蘭國小附幼", 120, 24.7, 121.7, 0, "宜蘭縣宜蘭市崇聖街2號"),
    Shelter("[YILAN] 羅東鎮立體育館", 800, 24.6, 121.7, 100, "宜蘭縣羅東鎮體育路15號"),
]
CATALOG = build_catalog(SHELTERS)


def line_of(s):
    # 跟 VectorStore.search 送進 prompt 的排版一致：真實文件、拿掉別名
    return f"- {ALIAS_PATTERN.sub('', VectorStore.build_document(s))}"


CONTEXT = "符合「宜蘭市」的避難所共 3 筆：\n" + "\n".join(line_of(s) for s in SHELTERS)


def test_target_line_does_not_match_a_longer_name_with_the_same_prefix():
    lines = CONTEXT.split("\n")
    assert target_line_index(lines, "宜蘭國小") == 1
    assert target_line_index(lines, "宜蘭國小附幼") == 2
    assert target_line_index(lines, "不存在國小") is None


def test_counterfactual_avoids_every_number_already_in_the_prompt():
    forbidden = {1037, 637}
    value = pick_counterfactual(500, forbidden)
    assert value not in forbidden and value != 500 and value >= 50


def test_swap_changes_only_the_target_line_and_keeps_it_consistent():
    perturbed = swap_number(CONTEXT, "羅東鎮立體育館", "羅東鎮立體育館")
    new = perturbed["counterfactual"]["capacity"]
    assert perturbed["original"] == {"capacity": 800, "remaining": 700}
    # 剩餘空位 = 新容量 - 目前收容，不然模型會因為矛盾改用別的數字
    assert perturbed["counterfactual"]["remaining"] == new - 100
    old_lines, new_lines = CONTEXT.split("\n"), perturbed["context"].split("\n")
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=True)) if a != b]
    assert changed == [3]
    assert f"容量 {new} 人" in new_lines[3] and "容量 800 人" not in new_lines[3]
    assert f"尚有 {new - 100} 個空位" in new_lines[3]
    assert 800 not in integers_in(perturbed["context"]) and 700 not in integers_in(perturbed["context"])


def test_swap_skips_when_target_was_not_retrieved():
    assert swap_number(CONTEXT, "台東縣立體育館") is None


def test_swap_scoring_separates_faithful_from_leaked():
    perturbed = swap_number(CONTEXT, "宜蘭國小")
    new = perturbed["counterfactual"]["capacity"]
    good = score_swap(f"宜蘭國小：宜蘭市，容量 {new} 人，剩餘空位 {new} 人", perturbed, "宜蘭國小")
    assert (good["faithful"], good["new_stated"], good["old_stated"]) == (1.0, 1.0, 0.0)
    leaked = score_swap("宜蘭國小：宜蘭市，容量 500 人，剩餘空位 500 人", perturbed, "宜蘭國小")
    assert (leaked["faithful"], leaked["old_stated"]) == (0.0, 1.0)
    assert leaked["numbers_supported"] == 0.0
    # 新舊都講（「原本 500 人，資料寫 N 人」）也不算忠實
    both = score_swap(f"容量 {new} 人（原 500 人）", perturbed, "宜蘭國小")
    assert both["faithful"] == 0.0


def test_old_value_that_is_also_in_the_question_is_not_a_leak():
    perturbed = swap_number(CONTEXT, "宜蘭國小", "容量 500 人的宜蘭國小")
    s = score_swap("容量 500 人的宜蘭國小沒有照資料講", perturbed, "容量 500 人的宜蘭國小")
    assert s["old_stated"] == 0.0


def test_drop_target_removes_only_the_target_and_has_no_header():
    hits = [Hit(name=s.name, document=VectorStore.build_document(s), distance=0.1 * i, metadata={})
            for i, s in enumerate(SHELTERS)]
    context = drop_target(hits, "[YILAN] 宜蘭國小", limit=10)
    assert "宜蘭國小是" not in context
    assert context.startswith("- 宜蘭國小附幼是")
    assert "別名" not in context
    assert drop_target(hits[:1], "[YILAN] 宜蘭國小", limit=10) is None
    assert len(drop_target(hits, "[YILAN] 宜蘭國小", limit=1).split("\n")) == 1


def test_drop_scoring():
    context = line_of(SHELTERS[2])
    target = "[YILAN] 宜蘭國小"

    honest = score_drop("資料中沒有宜蘭國小的資訊。", context, "宜蘭國小", target, 500, CATALOG)
    assert (honest["faithful"], honest["target_mentioned"], honest["acknowledged"]) == (1.0, 1.0, 1.0)

    # 拿問題裡的名稱硬湊一筆，連真實容量都講出來
    made_up = score_drop("宜蘭國小：宜蘭市，容量 500 人，剩餘空位 500 人。", context, "宜蘭國小", target, 500, CATALOG)
    assert (made_up["faithful"], made_up["fabricated"], made_up["old_stated"]) == (0.0, 1.0, 1.0)

    # 把別間的資料安到目標名下：數字都在 context 裡，但沒交代資料裡沒有
    misattributed = score_drop("宜蘭國小：容量 800 人。", context, "宜蘭國小", target, 500, CATALOG)
    assert misattributed["fabricated"] == 1.0 and misattributed["numbers_supported"] == 1.0

    # 「沒有」也可能出現在編造的句子裡；那時靠數字檢查抓
    sneaky = score_drop("宜蘭國小容量 640 人，目前沒有人收容。", context, "宜蘭國小", target, 500, CATALOG)
    assert sneaky["fabricated"] == 1.0

    # 實測 qwen2.5:7b 的典型失敗：問 A、直接答 B，沒說 A 不在資料裡。
    # 數字全對、名稱也真的存在，不算編造，但使用者會把 B 當成 A 的答案
    swapped = score_drop("羅東鎮立體育館：羅東鎮，容量 800 人。", context, "宜蘭國小", target, 500, CATALOG)
    assert (swapped["fabricated"], swapped["substituted"], swapped["faithful"]) == (0.0, 1.0, 0.0)

    # 先交代沒有、再推薦別間 → 忠實
    redirected = score_drop("資料中沒有宜蘭國小，附近的羅東鎮立體育館容量 800 人。",
                            context, "宜蘭國小", target, 500, CATALOG)
    assert (redirected["faithful"], redirected["substituted"]) == (1.0, 0.0)
