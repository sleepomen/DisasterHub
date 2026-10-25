import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.shelter_profile import facility_of, township_of  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data_for_refuge")
OUT_PATH = os.path.join(ROOT, "evals", "rag_eval.jsonl")

REGION_ZH = {"HUALIEN": "花蓮", "TAITUNG": "台東", "YILAN": "宜蘭"}


def load_shelters():
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        region = os.path.basename(path).split("_")[0].upper()
        with open(path, encoding="utf-8") as f:
            for item in json.load(f):
                addr = item.get("address", "").strip()
                tagged = f"[{region}] {item['name']}"
                rows.append({
                    "id": tagged,
                    "name": item["name"],
                    "region": region,
                    "region_zh": REGION_ZH[region],
                    "capacity": int(item["total_vessel"]),
                    "address": addr,
                    "township": item.get("township") or township_of(addr),
                    "facility": facility_of(tagged),
                })
    return rows


def case(query, relevant, category, note=""):
    return {"query": query, "relevant": sorted(relevant), "category": category, "note": note}


# 消防署資料有 644 間：名稱題只抽樣，題庫大小維持在原本 50 間時的規模
def exact_name_cases(rows):
    return [case(r["name"], [r["id"]], "exact_name") for i, r in enumerate(rows) if i % 15 == 0]


def name_variant_cases(rows):
    templates = ["{}在哪裡", "{}的容量多少", "{}還有空位嗎", "我想去{}避難", "{}目前收容幾人"]
    out = []
    for i, r in enumerate(rows):
        if i % 20 == 7:
            out.append(case(templates[i % len(templates)].format(r["name"]), [r["id"]], "name_variant"))
    return out


def region_cases(rows):
    templates = ["{}有哪些避難所", "{}縣的避難所", "{}地區可以去哪裡避難"]
    out = []
    for region, zh in REGION_ZH.items():
        ids = [r["id"] for r in rows if r["region"] == region]
        for t in templates:
            out.append(case(t.format(zh), ids, "region", f"{len(ids)} relevant"))
    return out


def township_cases(rows):
    by_town = {}
    for r in rows:
        if r["township"]:
            by_town.setdefault(r["township"], []).append(r["id"])
    out = []
    for i, (town, ids) in enumerate(sorted(by_town.items())):
        out.append(case(f"{town}的避難所", ids, "township", f"{len(ids)} relevant"))
        if i % 2 == 0:
            out.append(case(f"{town}有哪裡可以避難", ids, "township", f"{len(ids)} relevant"))
    return out


def facility_cases(rows):
    specs = [
        ("{}有哪些學校可以避難", {"國小", "國中", "高中"}),
        ("{}的國小避難所", {"國小"}),
        ("{}的國中", {"國中"}),
        ("{}的體育館", {"體育場館"}),
        ("{}的活動中心", {"活動中心"}),
        ("{}有廟可以避難嗎", {"宗教場所"}),
    ]
    out = []
    for region, zh in REGION_ZH.items():
        for template, facilities in specs:
            ids = [r["id"] for r in rows if r["region"] == region and r["facility"] in facilities]
            if ids:
                out.append(case(template.format(zh), ids, "facility", f"{len(ids)} relevant"))
    return out


def capacity_cases(rows):
    # 消防署資料的收容人數中位數 70 人：門檻改在 200 / 300（舊資料用的是 1000 / 500 / 300 以下）
    specs = [
        ("{}容量超過300人的避難所", lambda c: c > 300),
        ("{}能收200人以上的地方", lambda c: c >= 200),
    ]
    out = []
    for region, zh in REGION_ZH.items():
        for template, pred in specs:
            ids = [r["id"] for r in rows if r["region"] == region and pred(r["capacity"])]
            if ids:
                out.append(case(template.format(zh), ids, "capacity", f"{len(ids)} relevant"))
    return out


MANUAL_CASES = [
    # 別名：名稱的常見說法或簡稱（2026-10 換成消防署避難收容處所點位檔後重新標註）
    ("宜蘭大學體育館", ["[YILAN] 國立宜蘭大學體育館"], "alias"),
    ("宜蘭高中體育館", ["[YILAN] 國立宜蘭高中體育館"], "alias"),
    ("花蓮體育場", ["[HUALIEN] 花蓮縣立體育場"], "alias"),
    ("中正體育館", ["[HUALIEN] 中正體育館"], "alias"),
    ("台東市公所", ["[TAITUNG] 臺東市公所"], "alias"),
    ("淡江蘭陽校區", ["[YILAN] 淡江大學蘭陽校園"], "alias"),
    ("台東大學", ["[TAITUNG] 國立臺東大學"], "alias"),
    ("慈惠堂", ["[HUALIEN] 聖地慈惠堂", "[HUALIEN] 玉里慈惠堂"], "alias"),
    ("外役監", ["[HUALIEN] 光復自強外役監獄"], "alias"),
    ("805醫院", ["[HUALIEN] 國軍805醫院"], "alias"),
    ("蘭嶼高中", ["[TAITUNG] 蘭嶼完全高級中學"], "alias"),
    ("四維高中", ["[HUALIEN] 私立四維高級中學"], "alias"),
    ("羅東展演廳", ["[YILAN] 羅東鎮展演廳"], "alias"),
    ("馬蘭榮家", ["[TAITUNG] 行政院國軍退除役官兵輔導委員會馬蘭榮譽國民之家"], "alias"),
    ("原住民文化館", ["[HUALIEN] 新城鄉原住民文化館"], "alias"),
    ("慈濟大學", ["[HUALIEN] 慈濟大學人文社會學院", "[HUALIEN] 慈濟大學附設國民中學,小學"], "alias"),
    # 語意：地名、街道、最大
    ("知本附近", ["[TAITUNG] 知本金聯世紀酒店", "[TAITUNG] 知本社區活動中心"], "semantic"),
    ("礁溪", ["[YILAN] 礁溪國中", "[YILAN] 礁溪國小", "[YILAN] 礁溪鄉公所二樓"], "semantic"),
    ("南澳的學校", ["[YILAN] 南澳國小", "[YILAN] 南澳高中"], "semantic"),
    ("宜蘭最大的避難所", ["[YILAN] 公正國小體育館"], "semantic"),
    ("花蓮可以容納最多人的地方", ["[HUALIEN] 勝安宮"], "semantic"),
    ("林森路的避難所", ["[HUALIEN] 光復國中", "[HUALIEN] 花蓮市公所", "[HUALIEN] 森榮里辦公處"], "semantic"),
    ("慈惠三街", ["[HUALIEN] 勝安宮", "[HUALIEN] 聖地慈惠堂"], "semantic"),
    ("介仁街附近", ["[HUALIEN] 慈濟大學人文社會學院", "[HUALIEN] 慈濟大學附設國民中學,小學"], "semantic"),
    ("都歷路上", ["[TAITUNG] 成功鎮信義國民小學", "[TAITUNG] 都歷活動中心"], "semantic"),
    ("大武街", ["[TAITUNG] 大武村社區活動中心", "[TAITUNG] 大武老人會館",
              "[TAITUNG] 大武鄉多功能綜合活動中心暨避難收容所"], "semantic"),
    ("營區可以避難嗎", ["[HUALIEN] 復興南營區", "[HUALIEN] 南美崙營區"], "semantic"),
    ("廟宇可以避難嗎", ["[HUALIEN] 五穀宮", "[HUALIEN] 勝安宮", "[HUALIEN] 玉寶宮", "[HUALIEN] 花蓮港天宮",
                  "[YILAN] 大福補天宮", "[YILAN] 三聖宮", "[YILAN] 梅洲里鎮平宮", "[YILAN] 碧霞宮",
                  "[YILAN] 德陽宮弘道大樓", "[YILAN] 聖安宮", "[YILAN] 大坑里-協天宮"], "semantic"),
    # 負例：東部三縣以外、或跟避難所無關
    ("高雄的避難所", [], "negative"),
    ("台北車站附近哪裡可以避難", [], "negative"),
    ("今天天氣如何", [], "negative"),
    ("澎湖有避難所嗎", [], "negative"),
    ("如何申請災害補助", [], "negative"),
    ("桃園機場", [], "negative"),
    ("台中體育館", [], "negative"),
    ("新竹國小", [], "negative"),
    ("屏東縣立體育館", [], "negative"),
    ("嘉義市公所", [], "negative"),
]


def manual_cases(rows):
    known = {r["id"] for r in rows}
    out = []
    for query, relevant, category in MANUAL_CASES:
        missing = [r for r in relevant if r not in known]
        if missing:
            raise SystemExit(f"manual case '{query}' references unknown shelters: {missing}")
        out.append(case(query, relevant, category))
    return out


SUPERLATIVE_WORDS = ("最大", "最多", "容納最多")


def attach_expectations(cases, rows):
    """
    生成端評測用的關鍵事實，直接從來源資料算：
    每個相關避難所的容量（數字忠實度、單一避難所題目要講出容量），
    以及「最大 / 最多」排名題應該排第一的那間。
    """
    capacity = {r["id"]: r["capacity"] for r in rows}
    for c in cases:
        if not c["relevant"]:
            continue
        expect = {"capacity": {rid: capacity[rid] for rid in c["relevant"]}}
        if any(word in c["query"] for word in SUPERLATIVE_WORDS):
            expect["top1"] = max(c["relevant"], key=lambda rid: capacity[rid])
        c["expect"] = expect
    return cases


def build():
    rows = load_shelters()
    cases = (
        exact_name_cases(rows)
        + name_variant_cases(rows)
        + region_cases(rows)
        + township_cases(rows)
        + facility_cases(rows)
        + capacity_cases(rows)
        + manual_cases(rows)
    )
    seen = set()
    unique = []
    for c in cases:
        if c["query"] in seen:
            continue
        seen.add(c["query"])
        unique.append(c)
    return attach_expectations(unique, rows)


if __name__ == "__main__":
    cases = build()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    counts = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    print(f"wrote {len(cases)} cases to {OUT_PATH}")
    for cat, n in sorted(counts.items()):
        print(f"  {cat:<14}{n}")
