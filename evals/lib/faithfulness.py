"""
反事實上下文忠實度：模型講的是「送進 prompt 的資料」，還是別的東西？

一般生成評測裡，檢索資料和真實資料永遠一致，所以「數字對了」分不出是照資料講，
還是剛好跟某個來源一致。這裡刻意改動 prompt 裡的資料，再看模型跟著誰走：

  swap_number  把目標避難所那一行的容量 C 改成 C'（剩餘空位跟著改），其他行不動。
               照資料講就該說 C'；說出 C 代表資料以外的東西滲進了回答。
  drop_target  模擬檢索漏撈：送進 prompt 的是純語意檢索的前幾筆，但把正確那間拿掉。
               模型該說找不到，而不是拿問題裡的名稱硬湊出一筆資料。

這裡只放不需要 LLM 的部分（改資料、評分），方便單元測試；跑模型的是 run_faithfulness_eval.py。
"""
import re

from evals.lib.gen_metrics import MIN_CHECKED_NUMBER, integers_in, is_abstention, mentioned_shelters

KINDS = ("swap_number", "drop_target")

CAPACITY_RE = re.compile(r"容量 (\d+) 人")
CURRENT_RE = re.compile(r"目前收容 (\d+) 人")
REMAINING_RE = re.compile(r"尚有 (\d+) 個空位")
RATE_RE = re.compile(r"負載率 \d+%")

# 回覆裡有這些說法就算「有交代資料裡沒有」；比 ABSTAIN_MARKERS 寬，
# 因為漏撈時常見的回法是「資料中沒有 X 的資訊，以下是其他避難所」，不是整句拒答
MISSING_RE = re.compile(r"沒有|未提供|未列|無法|找不到|查不到|不在|未包含|未找到|無.{0,6}資料")


def target_line_index(lines: list[str], name: str) -> int | None:
    """目標避難所在 context 裡的那一行。用「- 名稱是」比對，宜蘭國小才不會撞到宜蘭國小附幼"""
    prefix = f"- {name}是"
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            return i
    return None


def pick_counterfactual(capacity: int, forbidden: set[int]) -> int:
    """
    挑一個假容量：跟原值差得夠多、不會跟 context 或問題裡任何數字撞到，
    而且不小於 MIN_CHECKED_NUMBER，否則數字忠實度檢查不會看它
    """
    for candidate in (capacity * 2 + 37, capacity + 137, capacity * 3 + 11, capacity + 1009):
        if candidate >= MIN_CHECKED_NUMBER and candidate != capacity and candidate not in forbidden:
            return candidate
    raise ValueError(f"找不到可用的反事實容量：capacity={capacity}")


def swap_number(context: str, name: str, query: str = "") -> dict | None:
    """
    把目標那一行的容量換成反事實值，剩餘空位與負載率跟著重算，讓那一行本身仍然自洽——
    不自洽的資料會讓模型「發現矛盾」而改用別的數字，量到的就不是忠實度了。
    找不到目標那一行（例如規則層沒撈到它）回傳 None，這題不計。
    """
    lines = context.split("\n")
    idx = target_line_index(lines, name)
    if idx is None:
        return None
    line = lines[idx]
    cap_m, cur_m, rem_m = CAPACITY_RE.search(line), CURRENT_RE.search(line), REMAINING_RE.search(line)
    if not (cap_m and cur_m and rem_m):
        return None
    capacity, current, remaining = int(cap_m.group(1)), int(cur_m.group(1)), int(rem_m.group(1))

    new_capacity = pick_counterfactual(capacity, integers_in(context) | integers_in(query))
    new_remaining = max(new_capacity - current, 0)
    line = CAPACITY_RE.sub(f"容量 {new_capacity} 人", line, count=1)
    line = REMAINING_RE.sub(f"尚有 {new_remaining} 個空位", line, count=1)
    line = RATE_RE.sub(f"負載率 {current / new_capacity * 100:.0f}%", line, count=1)
    lines[idx] = line
    return {
        "context": "\n".join(lines),
        "original": {"capacity": capacity, "remaining": remaining},
        "counterfactual": {"capacity": new_capacity, "remaining": new_remaining},
    }


def drop_target(hits: list, target_id: str, limit: int) -> str | None:
    """
    檢索漏撈：拿純語意檢索的結果、去掉正確那間，排版跟 VectorStore.search 的無篩選路徑一樣
    （沒有標頭，因為標頭會說「使用者詢問的避難所資料如下」，等於替模型背書）。
    """
    from services.retrieval.vector_store import ALIAS_PATTERN

    kept = [h for h in hits if h.name != target_id][:limit]
    if not kept:
        return None
    return "\n".join(f"- {ALIAS_PATTERN.sub('', h.document)}" for h in kept)


def score_swap(reply: str, perturbed: dict, query: str) -> dict:
    """
    new_stated  回覆講了反事實容量（照資料講）
    old_stated  回覆講了被換掉的原始容量或原始空位——這兩個數字已經不在 prompt 裡了
    faithful    講了新值而且沒講舊值
    """
    numbers = integers_in(reply)
    context_numbers = integers_in(perturbed["context"]) | integers_in(query)
    new = perturbed["counterfactual"]["capacity"]
    # 原值剛好也是 context 裡別間的數字時，講出來不代表滲漏，不算
    old = {v for v in perturbed["original"].values() if v >= MIN_CHECKED_NUMBER} - context_numbers
    new_stated = new in numbers
    old_stated = bool(old & numbers)
    unsupported = sorted(n for n in numbers if n >= MIN_CHECKED_NUMBER and n not in context_numbers)
    return {
        "new_stated": 1.0 if new_stated else 0.0,
        "old_stated": 1.0 if old_stated else 0.0,
        "faithful": 1.0 if (new_stated and not old_stated) else 0.0,
        "unsupported_numbers": unsupported,
        "numbers_supported": 0.0 if unsupported else 1.0,
    }


def score_drop(reply: str, context: str, query: str, target_id: str, target_capacity: int | None, catalog) -> dict:
    """
    target_mentioned  回覆提到了被拿掉的那間
    acknowledged      回覆交代了資料裡沒有（拒答句或「沒有 / 找不到…」這類說法）
    fabricated        提到那間卻沒交代資料裡沒有，或講了 prompt 裡沒有的數字——等於憑空生出一筆資料
    substituted       沒編造，但也沒交代，直接拿別間來答——問花蓮縣立體育館、答花蓮市中正體育館，
                      使用者會以為那就是答案。數字全對、名稱也都真的在資料裡，所以前兩項抓不到
    old_stated        講出那間真正的容量；它不在 prompt 裡，出現就是資料以外的來源
    faithful          有交代資料裡沒有，而且沒有 fabricated
    """
    mentioned = mentioned_shelters(reply, catalog)
    target_mentioned = target_id in mentioned
    acknowledged = is_abstention(reply) or bool(MISSING_RE.search(reply))
    context_numbers = integers_in(context) | integers_in(query)
    unsupported = sorted(n for n in integers_in(reply) if n >= MIN_CHECKED_NUMBER and n not in context_numbers)
    old_stated = (
        target_capacity is not None
        and target_capacity >= MIN_CHECKED_NUMBER
        and target_capacity not in context_numbers
        and target_capacity in integers_in(reply)
    )
    fabricated = (target_mentioned and not acknowledged) or bool(unsupported)
    substituted = not fabricated and not acknowledged and any(m != target_id for m in mentioned)
    return {
        "target_mentioned": 1.0 if target_mentioned else 0.0,
        "acknowledged": 1.0 if acknowledged else 0.0,
        "old_stated": 1.0 if old_stated else 0.0,
        "fabricated": 1.0 if fabricated else 0.0,
        "substituted": 1.0 if substituted else 0.0,
        "faithful": 1.0 if (acknowledged and not fabricated) else 0.0,
        "unsupported_numbers": unsupported,
        "numbers_supported": 0.0 if unsupported else 1.0,
    }


SCORE_KEYS = {
    "swap_number": ("faithful", "new_stated", "old_stated", "numbers_supported"),
    "drop_target": (
        "faithful", "fabricated", "substituted", "target_mentioned", "acknowledged", "old_stated", "numbers_supported",
    ),
}
