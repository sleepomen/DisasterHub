"""
回答後的防護：刪掉提到「送進 prompt 的資料裡沒有」的避難所的那幾行。

換成消防署資料後，llama3.2:3b 在只送一間避難所的點名題上會自己往下湊清單：
問「新城社區活動中心」，答完那一間又列出「花蓮社區活動中心 50 人」「宜蘭社區活動中心 30 人」，
名稱跟數字都是編的（43 題點名題裡 7 題）。資料裡一大半是「XX社區活動中心」這種模板化的名稱，
模型很容易照著模板生。prompt 已經寫了「問某一間時只回答那一間」，3B 模型照樣不聽，
所以改成由程式在輸出端擋：一行裡有看起來像場所名稱、卻對不上資料裡任何一間的片段，整行拿掉。

注意：生成評測的幻覺偵測（evals/lib/gen_metrics.py）跟這裡做的是類似的判斷。
評測量到的幻覺下降有一部分是「同一類規則擋掉了同一類規則會抓的東西」，不能全部算成模型變好
"""
import re

from services.retrieval.shelter_profile import normalize_place

# 場所名稱的字尾。比 shelter_profile 的設施分類寬：這裡要抓的是「看起來像一個地方」，
# 包含資料裡沒有的類型（模型編出來的不一定是現有類型）
PLACE_SUFFIXES = sorted({
    "國民小學", "國民中學", "高級中學", "國小", "國中", "高中", "女中", "大學", "幼兒園", "托兒所",
    "體育館", "體育場", "運動場", "運動中心", "圖書館", "公所", "辦公處", "活動中心", "文康中心",
    "會館", "文化館", "教會", "收容站", "收容所", "營區",
}, key=len, reverse=True)
# 字首不含助詞與標點；「1. 和平社區活動中心」抓到的是「和平社區活動中心」
_NAME_CHAR = r"(?:(?![的有是在與和為到去從於或及個間位該這那此各等某其目前以下共約])[一-鿿A-Za-z0-9])"
# 字尾後面接「路／街」是路名不是場所：「宜蘭市女中路一段148號」不該抓出「宜蘭市女中」
PLACE_NAME_RE = re.compile(rf"{_NAME_CHAR}{{1,12}}(?:{'|'.join(PLACE_SUFFIXES)})(?![路街巷])")


def supported_names(context: str, known_names: list[str]) -> list[str]:
    """資料裡出現的避難所名稱（顯示名稱或別名）"""
    return [name for name in known_names if name and name in context]


def is_supported(fragment: str, names: list[str]) -> bool:
    """
    片段跟資料裡某一間的名稱互相包含就算有根據：
    「礁溪鄉公所」是「礁溪鄉公所二樓」的前段，「和平社區活動中心」可能被抓成「平社區活動中心」
    """
    fragment = normalize_place(fragment)
    # 臺 / 台 不算不同：資料寫「國立臺東大學」，模型常寫成「台東大學」
    return any(fragment in n or n in fragment for n in map(normalize_place, names))


def unsupported_fragments(line: str, names: list[str]) -> list[str]:
    return [m.group(0) for m in PLACE_NAME_RE.finditer(line) if not is_supported(m.group(0), names)]


def filter_lines(text: str, names: list[str]) -> tuple[str, list[str]]:
    """回傳 (留下的文字, 被拿掉的行)。全部都會被拿掉時原樣保留：給空白回答比給可疑的回答更糟"""
    kept, dropped = [], []
    for line in text.split("\n"):
        (dropped if unsupported_fragments(line, names) else kept).append(line)
    if dropped and not any(line.strip() for line in kept):
        return text, []
    return "\n".join(kept), dropped


class LineGuard:
    """
    串流版：片段先累積到換行才判斷整行，判斷完才送出。
    使用者看到的文字會晚一行出現，換到的是編造的那一行永遠不會出現在畫面上
    """

    def __init__(self, names: list[str]):
        self.names = names
        self.buffer = ""
        self.emitted_any = False
        self.dropped: list[str] = []
        self.original = ""

    def feed(self, chunk: str) -> str:
        self.original += chunk
        self.buffer += chunk
        out = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            out.append(self._check(line + "\n"))
        return "".join(out)

    def finish(self) -> str:
        tail = self._check(self.buffer) if self.buffer else ""
        self.buffer = ""
        if self.dropped and not self.emitted_any:
            # 每一行都被擋：跟 filter_lines 一樣原樣給，不給空白
            self.dropped = []
            return self.original
        return tail

    def _check(self, line: str) -> str:
        if unsupported_fragments(line, self.names):
            self.dropped.append(line.rstrip("\n"))
            return ""
        if line.strip():
            self.emitted_any = True
        return line
