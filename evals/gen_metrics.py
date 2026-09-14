"""
生成端評測的判定邏輯。

檢索評測量的是「該給模型看的資料有沒有撈到」，這裡量的是「模型最後講出來的話對不對」：
- 回答召回率：標準答案的避難所有多少比例真的被講出來
- 幻覺：講了檢索資料裡沒有的避難所，或編造了不存在的名稱
- 數字忠實度：回覆裡的容量 / 收容數字是否都出現在檢索資料裡
- 拒答：範圍外或無資料時要明說沒有資料，而且不能列避難所
- 格式：只能繁體中文、不能有 emoji、不能被 num_predict 截斷

全部用程式規則判定，不靠另一個 LLM 當裁判；本機的小模型當裁判不可靠，規則判定也才能重現。
"""
import re
from dataclasses import dataclass, field
from statistics import mean

from services.shelter_profile import aliases_of, strip_region_tag

# 回覆裡「說沒有資料」的講法；出現任何一個就視為拒答
ABSTAIN_MARKERS = ("沒有相關資料", "本系統只涵蓋", "目前沒有相關", "無相關資料", "查無", "沒有找到", "尚未執行")

# 明確是簡體、繁體裡不會單獨出現的字。像「后」「里」這種繁簡共用的不放
SIMPLIFIED_CHARS = set(
    "个们这为会来对时从说国学门车长东体馆区县镇乡运场应难资讯没进过还发办览际业务观点线组织"
    "给让请据处间产设备实现总数员级团队达标条规导则议开关灾离远选择该预计"
)

ENGLISH_RE = re.compile(r"[A-Za-z]+")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕]")
# 整數，跳過小數（座標 23.98 不能被拆成 23 和 98）；允許千分位逗號
INTEGER_RE = re.compile(r"(?<![\d.])(\d[\d,]*)(?!\.?\d)")

# 抓「看起來像避難所名稱」的片段，用來找編造的名稱。
# 前綴不含助詞（的 / 有 / 是 …），「宜蘭的體育館」才不會整段被當成一個名稱
_NAME_CHAR = r"(?:(?![的有是在與和為到去從於或及個間位該這那此各等某其目前])[一-鿿])"
FACILITY_NAME_RE = re.compile(
    _NAME_CHAR + r"{2,10}(?:國小附幼|國小|國中|高中|女中|體育館|體育場|運動中心|綜合運動場|運動場|圖書館|公所|文化會館|會館|活動中心)"
)

# 回覆裡小於這個值的整數（序號、筆數、百分比）不做忠實度檢查
MIN_CHECKED_NUMBER = 50

# 問題裡出現這些詞，或直接只打避難所名稱（exact_name 類）時，回覆才必須講出容量；
# 「X 在哪裡」只答地址是對的，不該因為沒講容量被扣分
CAPACITY_WORDS = ("容量", "空位", "收容", "幾人", "多少人", "可以收", "能收", "容納")


def asks_capacity(query: str, category: str | None = None) -> bool:
    return category == "exact_name" or any(word in query for word in CAPACITY_WORDS)


@dataclass
class ShelterEntry:
    id: str          # 帶地區標籤的完整名稱，與檢索評測的 relevant 對齊
    name: str        # 去掉標籤後的顯示名稱，也是模型回覆裡會出現的字串
    aliases: list[str] = field(default_factory=list)

    @property
    def surface_forms(self) -> list[str]:
        return [self.name] + self.aliases


def build_catalog(shelters) -> list[ShelterEntry]:
    """
    別名只留有辨識度的：像「立體育館」這種同時是好幾間名稱子字串的別名，
    拿來比對回覆會把別間也算成有提到，所以是其他避難所名稱子字串的別名一律不用。
    """
    names = {s.name: strip_region_tag(s.name) for s in shelters}
    catalog = []
    for s in shelters:
        others = [n for sid, n in names.items() if sid != s.name]
        aliases = [a for a in aliases_of(s.name) if not any(a in other for other in others)]
        catalog.append(ShelterEntry(id=s.name, name=names[s.name], aliases=aliases))
    return catalog


def mentioned_shelters(text: str, catalog: list[ShelterEntry]) -> list[str]:
    """文字裡提到的避難所 id，依第一次出現的位置排序（排名題要看誰先被講）"""
    found = []
    for entry in catalog:
        positions = [text.find(form) for form in entry.surface_forms if form and form in text]
        if positions:
            found.append((min(positions), entry.id))
    return [entry_id for _, entry_id in sorted(found)]


def suspected_invented_names(text: str, catalog: list[ShelterEntry], query: str = "") -> list[str]:
    """
    看起來像避難所名稱、卻對不上任何已知名稱或別名的片段。
    使用者自己在問題裡寫的詞（「縣立體育館的資訊如下」）是複述不是編造，不算。
    """
    known = [form for entry in catalog for form in entry.surface_forms if form]
    out = []
    for match in FACILITY_NAME_RE.finditer(text):
        fragment = match.group(0)
        if any(form in fragment for form in known):
            continue
        if query and fragment in query:
            continue
        if fragment not in out:
            out.append(fragment)
    return out


def integers_in(text: str) -> set[int]:
    return {int(m.replace(",", "")) for m in INTEGER_RE.findall(text or "")}


def is_abstention(text: str) -> bool:
    return any(marker in text for marker in ABSTAIN_MARKERS)


def format_issues(text: str) -> list[str]:
    issues = []
    if ENGLISH_RE.search(text):
        issues.append("english")
    if EMOJI_RE.search(text):
        issues.append("emoji")
    simplified = sorted({ch for ch in text if ch in SIMPLIFIED_CHARS})
    if simplified:
        issues.append("simplified:" + "".join(simplified))
    return issues


def score_reply(
    reply: str,
    relevant: list[str],
    context: str,
    catalog: list[ShelterEntry],
    expect: dict | None = None,
    truncated: bool = False,
    query: str = "",
    check_capacity: bool = True,
) -> dict:
    """
    單題評分。relevant 為空代表負例（應該拒答）。
    query 用來排除「複述問題用詞」的誤判；check_capacity 決定要不要檢查有沒有講出容量。
    回傳的每個欄位都是 0 / 1 或比例，方便直接平均。
    """
    expect = expect or {}
    relevant_set = set(relevant)
    mentioned = mentioned_shelters(reply, catalog)
    mentioned_set = set(mentioned)
    context_set = set(mentioned_shelters(context, catalog)) if context else set()
    invented = suspected_invented_names(reply, catalog, query)
    abstained = is_abstention(reply)
    issues = format_issues(reply)

    row = {
        "mentioned": mentioned,
        "invented": invented,
        "abstained": abstained,
        "format_issues": issues,
        "format_ok": 0.0 if issues else 1.0,
        "truncated": 1.0 if truncated else 0.0,
    }

    if not relevant_set:
        # 負例：要拒答，而且不能講出任何避難所或編名字
        row["abstain_correct"] = 1.0 if (abstained and not mentioned_set and not invented) else 0.0
        return row

    hits = mentioned_set & relevant_set
    row["answer_recall"] = len(hits) / len(relevant_set)
    row["answer_precision"] = len(hits) / len(mentioned_set) if mentioned_set else 0.0
    row["complete"] = 1.0 if hits == relevant_set else 0.0
    # 講了檢索資料裡沒有的已知避難所，或編了名字，都算幻覺
    unsupported = (mentioned_set - context_set) if context else set()
    row["unsupported_mentions"] = sorted(unsupported)
    row["hallucinated"] = 1.0 if (unsupported or invented) else 0.0
    # 錯誤拒答：明明有資料卻說沒有
    row["false_abstain"] = 1.0 if (abstained and not hits) else 0.0

    # 數字忠實度：回覆裡 ≥ MIN_CHECKED_NUMBER 的整數都要能在檢索資料或問題裡找到
    # （「超過 500 人的避難所有…」複述問題裡的 500 不算編造）
    reply_numbers = {n for n in integers_in(reply) if n >= MIN_CHECKED_NUMBER}
    supported_numbers = integers_in(context) | integers_in(query)
    unsupported_numbers = sorted(reply_numbers - supported_numbers)
    row["unsupported_numbers"] = unsupported_numbers
    row["numbers_supported"] = 1.0 if not unsupported_numbers else 0.0
    row["numbers_checked"] = len(reply_numbers)

    # 關鍵事實：單一避難所、而且問的是容量 / 空位 / 收容的題目，回覆有沒有講出它的容量
    capacities = expect.get("capacity") or {}
    if check_capacity and len(relevant_set) == 1 and capacities:
        (target,) = relevant_set
        expected = capacities.get(target)
        row["capacity_stated"] = 1.0 if (expected is not None and expected in integers_in(reply)) else 0.0

    # 排名題：第一個被講出來的避難所要是正確的第一名
    top1 = expect.get("top1")
    if top1:
        row["top1_correct"] = 1.0 if (mentioned and mentioned[0] == top1) else 0.0
    return row


POSITIVE_KEYS = (
    "answer_recall", "answer_precision", "complete", "hallucinated", "false_abstain",
    "numbers_supported", "capacity_stated", "top1_correct", "format_ok", "truncated",
)
NEGATIVE_KEYS = ("abstain_correct", "format_ok", "truncated")


def aggregate(rows: list[dict], keys=POSITIVE_KEYS) -> dict:
    out = {}
    for key in keys:
        values = [r[key] for r in rows if key in r]
        if values:
            out[key] = mean(values)
    return out
