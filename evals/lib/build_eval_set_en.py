"""
英文檢索題庫：把 evals/datasets/rag_eval.jsonl 逐題換成英文問法，正確答案不動，
寫到 evals/datasets/rag_eval_en.jsonl。一題對一題，所以中英文兩次評測可以逐題配對比較
（evals/lib/compare_runs.py）。

- 模板產生的題目（名稱、鄉鎮、地區、設施、容量）照句型換：鄉鎮用官方英文名稱，
  避難所名稱保留中文——資料沒有英文名稱，使用者只能照招牌寫中文
- 別名、語意題、負例是手寫的，這裡也手寫英文版，照英文使用者真的會怎麼寫：
  「Hualien Stadium」「Shelters on Linsen Road」。路名、拼音小地名這些改寫層還不認得的寫法
  照樣放進來，分數才會反映真實的缺口
- 有任何一題沒對到句型就報錯，不會默默少題

    python evals/lib/build_eval_set_en.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from services.web.i18n import REGION_EN, TOWNSHIP_EN  # noqa: E402

SRC = os.path.join(ROOT, "evals", "datasets", "rag_eval.jsonl")
OUT = os.path.join(ROOT, "evals", "datasets", "rag_eval_en.jsonl")

REGION = {"宜蘭": "Yilan", "花蓮": "Hualien", "台東": "Taitung"}

# 手寫的題目：中文 → 英文
HANDWRITTEN = {
    # region（每個縣三種問法）
    **{f"{zh}有哪些避難所": f"Which shelters are in {en}?" for zh, en in REGION.items()},
    **{f"{zh}縣的避難所": f"Shelters in {en} County" for zh, en in REGION.items()},
    **{f"{zh}地區可以去哪裡避難": f"Where can I go to take shelter in the {en} area?" for zh, en in REGION.items()},
    # facility
    **{f"{zh}有哪些學校可以避難": f"Which schools in {en} can be used as shelters?" for zh, en in REGION.items()},
    **{f"{zh}的國小避難所": f"Elementary school shelters in {en}" for zh, en in REGION.items()},
    **{f"{zh}的國中": f"Junior high schools in {en}" for zh, en in REGION.items()},
    **{f"{zh}的體育館": f"Gyms in {en}" for zh, en in REGION.items()},
    **{f"{zh}的活動中心": f"Community centers in {en}" for zh, en in REGION.items()},
    **{f"{zh}有廟可以避難嗎": f"Are there temples in {en} that can be used as shelters?" for zh, en in REGION.items()},
    # capacity
    **{f"{zh}容量超過300人的避難所": f"Shelters in {en} with a capacity of more than 300 people" for zh, en in REGION.items()},
    **{f"{zh}能收200人以上的地方": f"Places in {en} that can hold at least 200 people" for zh, en in REGION.items()},
    # alias：有通行英文名稱的寫英文，其餘照招牌寫中文
    "宜蘭大學體育館": "National Ilan University gymnasium",
    "宜蘭高中體育館": "Yilan Senior High School gym",
    "花蓮體育場": "Hualien Stadium",
    "中正體育館": "Where is 中正體育館?",
    "台東市公所": "Taitung City Office",
    "淡江蘭陽校區": "Tamkang University Lanyang Campus",
    "台東大學": "National Taitung University",
    "慈惠堂": "Where is 慈惠堂?",
    "外役監": "Where is 外役監?",
    "805醫院": "Is 805醫院 a shelter?",
    "蘭嶼高中": "Lanyu High School",
    "四維高中": "Siwei High School",
    "羅東展演廳": "Luodong Performance Hall",
    "馬蘭榮家": "Malan Veterans Home",
    "原住民文化館": "Indigenous culture hall",
    "慈濟大學": "Tzu Chi University",
    # semantic
    "知本附近": "Shelters near Zhiben",
    "礁溪": "Jiaoxi",
    "南澳的學校": "Schools in Nan'ao",
    "宜蘭最大的避難所": "The largest shelter in Yilan",
    "花蓮可以容納最多人的地方": "Which place in Hualien can hold the most people?",
    "林森路的避難所": "Shelters on Linsen Road",
    "慈惠三街": "Cihui 3rd Street",
    "介仁街附近": "Near Jieren Street",
    "都歷路上": "On Duli Road",
    "大武街": "Dawu Street",
    "營區可以避難嗎": "Can military camps be used as shelters?",
    "廟宇可以避難嗎": "Can temples be used as shelters?",
    # negative
    "高雄的避難所": "Shelters in Kaohsiung",
    "台北車站附近哪裡可以避難": "Where can I take shelter near Taipei Main Station?",
    "今天天氣如何": "What's the weather like today?",
    "澎湖有避難所嗎": "Are there shelters in Penghu?",
    "如何申請災害補助": "How do I apply for disaster relief subsidies?",
    "桃園機場": "Taoyuan Airport",
    "台中體育館": "Taichung gymnasium",
    "新竹國小": "Hsinchu elementary school",
    "屏東縣立體育館": "Pingtung County Stadium",
    "嘉義市公所": "Chiayi City Office",
}

TOWNSHIP = TOWNSHIP_EN
TOWNSHIP_PATTERNS = [
    (re.compile(r"^(.+[鄉鎮市])的避難所$"), "Shelters in {}"),
    (re.compile(r"^(.+[鄉鎮市])有哪裡可以避難$"), "Where can I take shelter in {}?"),
]
VACANCY = re.compile(r"^(.+)還有空位嗎$")


def translate(case: dict) -> str:
    query, category = case["query"], case["category"]
    if query in HANDWRITTEN:
        return HANDWRITTEN[query]
    if category == "exact_name":
        return f"Where is {query}?"
    if category == "name_variant":
        m = VACANCY.match(query)
        if m:
            return f"Does {m.group(1)} have any free spaces?"
    if category == "township":
        for pattern, template in TOWNSHIP_PATTERNS:
            m = pattern.match(query)
            if m and m.group(1).replace("臺", "台") in TOWNSHIP:
                return template.format(TOWNSHIP[m.group(1).replace("臺", "台")])
    raise SystemExit(f"沒有英文版：[{category}] {query}")


def main() -> int:
    with open(SRC, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    assert set(REGION) == set(REGION_EN), "REGION 要跟 services/web/i18n.py 的三個縣一致"
    out = []
    for case in cases:
        en = dict(case)
        en["query"] = translate(case)
        en["query_zh"] = case["query"]
        out.append(en)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for case in out:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"{len(out)} 題 → {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
