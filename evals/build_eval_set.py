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
                    "township": township_of(addr),
                    "facility": facility_of(tagged),
                })
    return rows


def case(query, relevant, category, note=""):
    return {"query": query, "relevant": sorted(relevant), "category": category, "note": note}


def exact_name_cases(rows):
    return [case(r["name"], [r["id"]], "exact_name") for i, r in enumerate(rows) if i % 5 in (0, 2)]


def name_variant_cases(rows):
    templates = ["{}在哪裡", "{}的容量多少", "{}還有空位嗎", "我想去{}避難", "{}目前收容幾人"]
    out = []
    for i, r in enumerate(rows):
        if i % 10 in (1, 4, 7):
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
    ]
    out = []
    for region, zh in REGION_ZH.items():
        for template, facilities in specs:
            ids = [r["id"] for r in rows if r["region"] == region and r["facility"] in facilities]
            if ids:
                out.append(case(template.format(zh), ids, "facility", f"{len(ids)} relevant"))
    return out


def capacity_cases(rows):
    specs = [
        ("{}能收上千人的地方", lambda c: c >= 1000),
        ("{}容量超過500人的避難所", lambda c: c > 500),
        ("{}的小型避難所，容量300人以下", lambda c: c <= 300),
    ]
    out = []
    for region, zh in REGION_ZH.items():
        for template, pred in specs:
            ids = [r["id"] for r in rows if r["region"] == region and pred(r["capacity"])]
            if ids:
                out.append(case(template.format(zh), ids, "capacity", f"{len(ids)} relevant"))
    return out


MANUAL_CASES = [
    ("縣立體育館", ["[HUALIEN] 花蓮縣立體育館", "[TAITUNG] 台東縣立體育館"], "alias"),
    ("花蓮體育館", ["[HUALIEN] 花蓮縣立體育館", "[HUALIEN] 花蓮市中正體育館"], "alias"),
    ("中正體育館", ["[HUALIEN] 花蓮市中正體育館"], "alias"),
    ("台東體育場", ["[TAITUNG] 台東縣立體育場"], "alias"),
    ("羅東體育館", ["[YILAN] 羅東鎮立體育館"], "alias"),
    ("蘇澳體育館", ["[YILAN] 蘇澳鎮立體育館"], "alias"),
    ("宜蘭運動中心", ["[YILAN] 宜蘭市國民運動中心"], "alias"),
    ("運動公園的體育館", ["[YILAN] 宜蘭運動公園體育館"], "alias"),
    ("原民會館", ["[TAITUNG] 台東市原住民文化會館"], "alias"),
    ("原住民文化館", ["[TAITUNG] 台東市原住民文化會館"], "alias"),
    ("花蓮市政府可以避難嗎", ["[HUALIEN] 花蓮市公所"], "alias"),
    ("台東市政府", ["[TAITUNG] 台東市公所"], "alias"),
    ("女子中學", ["[TAITUNG] 台東女中"], "alias"),
    ("台東的高中職", ["[TAITUNG] 台東高中", "[TAITUNG] 台東女中"], "alias"),
    ("圖書館可以避難嗎", ["[HUALIEN] 花蓮市立圖書館"], "alias"),
    ("有附設幼兒園的避難所", ["[YILAN] 中興國小附幼"], "alias"),
    ("知本附近", ["[TAITUNG] 知本國小"], "semantic"),
    ("馬蘭那邊有避難所嗎", ["[TAITUNG] 馬蘭國小"], "semantic"),
    ("礁溪", ["[YILAN] 礁溪國中"], "semantic"),
    ("南澳有哪裡可以躲", ["[YILAN] 南澳鄉綜合體育場", "[YILAN] 澳花國小"], "semantic"),
    ("三星鄉的收容地點", ["[YILAN] 三星國中", "[YILAN] 三星鄉綜合運動場"], "semantic"),
    ("頭城鎮附近的學校", ["[YILAN] 頭城國小", "[YILAN] 二城國小"], "semantic"),
    ("冬山", ["[YILAN] 清溝國小"], "semantic"),
    ("五結鄉", ["[YILAN] 中興國小附幼"], "semantic"),
    ("壯圍有哪些", ["[YILAN] 壯圍國小", "[YILAN] 壯圍國中"], "semantic"),
    ("大同鄉可以去哪", ["[YILAN] 大同鄉公所", "[YILAN] 南山國小"], "semantic"),
    ("花蓮市區最大的收容場所", ["[HUALIEN] 花蓮縣立體育館"], "semantic"),
    ("台東可以容納最多人的地方", ["[TAITUNG] 台東縣立體育館"], "semantic"),
    ("宜蘭最大的避難所", ["[YILAN] 宜蘭市國民運動中心"], "semantic"),
    ("鄉公所或市公所", ["[HUALIEN] 花蓮市公所", "[TAITUNG] 台東市公所", "[YILAN] 大同鄉公所"], "semantic"),
    ("桂林北路的避難所", ["[TAITUNG] 台東縣立體育館", "[TAITUNG] 台東縣立體育場"], "semantic"),
    ("中華路一段附近", ["[TAITUNG] 台東高中", "[TAITUNG] 東海國中"], "semantic"),
    ("更生路上", ["[TAITUNG] 卑南國中", "[TAITUNG] 新生國小"], "semantic"),
    ("達固湖灣大路", ["[HUALIEN] 花蓮縣立體育館"], "semantic"),
    ("四維路", ["[TAITUNG] 台東女中", "[TAITUNG] 寶桑國小"], "semantic"),
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
