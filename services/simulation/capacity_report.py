"""
容量缺口報告的整理與輸出（tools/capacity_report.py 負責跑情境、讀寫資料庫）。

兩部分：
1. 各鄉鎮自有容量：只用資料，不用任何假設。登記容量能收下鄉鎮人口的幾 %，
   強震、淹水分開算（淹水不算位在土石流潛勢範圍內的、兩者都不算沒列為這類災害避難收容處所的）。
2. 情境模擬：跟地圖上的模擬同一套（以鄉鎮人口重心為中心、最小成本流、路網、警戒封路），
   疏散比例是假設，所以未安置人數一律給 ×0.8～×1.2 的範圍，另外給「所有受影響的人都疏散時最多能安置多少」，
   這個上限不受疏散比例影響
"""
from services.simulation.evacuation import DESIGNATED_HAZARD, undesignated

# 疏散比例的不確定範圍：戶籍人口跟當下實際在那裡的人不同（evals/run_sensitivity_eval.py 同一組）
DEMAND_FACTORS = (0.8, 1.0, 1.2)
KIND_LABELS = {"earthquake": "強震", "flood": "淹水", "flood_red": "淹水＋紅色警戒"}
COUNTY_ORDER = ("宜蘭縣", "花蓮縣", "臺東縣")
# 鄉鎮自有容量低於這個比例就列為缺口最大的鄉鎮
LOW_SHARE = 0.05
TOP = 10


def _county_key(county: str) -> int:
    county = county.replace("台", "臺")
    return COUNTY_ORDER.index(county) if county in COUNTY_ORDER else len(COUNTY_ORDER)


def township_capacity(townships: list[dict], shelters: list, unusable: dict[str, set[str]] | None = None) -> list[dict]:
    """
    每個鄉鎮：人口、避難所數、登記容量，以及強震、淹水時能用的容量占人口幾 %。
    unusable 是 {災害類型: 這類災害不能去的避難所名稱}（淹水時位在土石流潛勢範圍內的）。
    鄉鎮名稱在宜花東三縣沒有重複，避難所的 township 欄位直接對得上
    """
    unusable = unusable or {}
    by_town: dict[str, list] = {}
    for s in shelters:
        by_town.setdefault(s.township, []).append(s)
    rows = []
    for t in sorted(townships, key=lambda t: (_county_key(t["county"]), t["name"])):
        own = by_town.get(t["name"], [])
        pop = int(t["pop"])
        row = {
            "county": t["county"], "township": t["name"], "population": pop,
            "shelters": len(own), "capacity": sum(s.capacity for s in own),
            "vulnerable_capacity": sum(s.capacity for s in own if s.vulnerable_ok),
            "zero_capacity": sorted(s.name for s in own if s.capacity <= 0),
            "hazards": {},
        }
        for kind in DESIGNATED_HAZARD:
            skip = set(undesignated(own, kind)) | unusable.get(kind, set())
            usable = [s for s in own if s.name not in skip]
            capacity = sum(s.capacity for s in usable)
            row["hazards"][kind] = {
                "shelters": len(usable), "capacity": capacity,
                "share": round(capacity / pop, 4) if pop else None,
            }
        rows.append(row)
    return rows


def nobody_placed(evacuees: int) -> dict:
    """圈內沒有任何一間避難所能去時的分配結果（跟 plan_evacuation 同樣的欄位）"""
    return {"assigned": {}, "placed": 0, "unplaced": int(evacuees), "cut_off": [], "unreachable": [],
            "distance_basis": "none"}


def summarize_scenario(
    town: dict, kind: str, radius_km: float, population: dict, destinations: list,
    plans: dict, excluded: int = 0, not_designated: int = 0, alerts: int = 0,
) -> dict:
    """
    一個情境的結果。plans 是 {疏散人數倍率: plan_evacuation 的結果}，另外 "all" 是所有受影響的人都疏散時的結果。
    滿載、沒用到的避難所看基準（倍率 1.0）的分配；被切斷的村里看 "all"（人越多，沒有路可走的村里越不會漏掉）
    """
    base, everyone = plans[1.0], plans["all"]
    weights = {v["name"]: v["weight"] for v in population.get("villages", [])}
    capacity = {s.name: s.capacity for s in destinations}
    covered = int(population["covered_population"])
    return {
        "township": town["name"], "county": town["county"], "kind": kind, "radius_km": radius_km,
        "center": [town["lat"], town["lon"]],
        "covered_population": covered,
        "evacuation_ratio": population["evacuation_ratio"],
        "destinations": len(destinations), "capacity": sum(capacity.values()),
        "excluded": excluded, "undesignated": not_designated, "red_alerts": alerts,
        "evacuees": {str(f): plans[f]["placed"] + plans[f]["unplaced"] for f in DEMAND_FACTORS},
        "unplaced": {str(f): plans[f]["unplaced"] for f in DEMAND_FACTORS},
        "placed_max": everyone["placed"],
        # 不管疏散比例怎麼假設，這一圈最多就是能讓這個比例的受影響人口有地方去
        "max_ratio": round(everyone["placed"] / covered, 4) if covered else None,
        "cut_off": sorted(({"village": v, "population": weights.get(v, 0)} for v in everyone["cut_off"]),
                          key=lambda c: (-c["population"], c["village"])),
        "unreachable": sorted(everyone["unreachable"]),
        "used": sorted(n for n in capacity if base["assigned"].get(n, 0) > 0),
        "full": sorted(n for n, c in capacity.items() if c > 0 and base["assigned"].get(n, 0) >= c),
        "unused": sorted(n for n, c in capacity.items() if c > 0 and base["assigned"].get(n, 0) == 0),
        "distance_basis": everyone["distance_basis"],
    }


def shelter_usage(scenarios: list[dict]) -> dict[str, dict]:
    """每間避難所在幾個情境裡分到人、滿載、是目的地卻一個人都沒分到（只看強震與淹水，紅色警戒另外看）"""
    usage: dict[str, dict] = {}
    for sc in scenarios:
        if sc["kind"] == "flood_red":
            continue
        for key in ("used", "full", "unused"):
            for name in sc[key]:
                usage.setdefault(name, {"used": 0, "full": 0, "unused": 0})[key] += 1
    return usage


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _people(n: int) -> str:
    return f"{n:,}"


def _range(sc: dict) -> str:
    low, mid, high = (sc["unplaced"][str(f)] for f in DEMAND_FACTORS)
    if high == 0:
        return "0"
    text = f"{_people(mid)}（{_people(low)}～{_people(high)}）"
    return f"全部 {text}" if not sc["destinations"] else text


def _max(sc: dict) -> str:
    return "0%（沒有能去的避難所）" if not sc["destinations"] else _pct(sc["max_ratio"])


def no_designated(towns: list[dict], kind: str) -> list[dict]:
    """有人口、卻沒有任何一間避難所列為這類災害適用的鄉鎮"""
    return [t for t in towns if t["population"] and t["hazards"][kind]["shelters"] == 0]


def cut_off_villages(scenarios: list[dict]) -> dict[str, int]:
    """紅色警戒時被切斷的村里 → 受影響人口（同一個村里在好幾個情境被切斷只算一次，取最大的）"""
    out: dict[str, int] = {}
    for sc in scenarios:
        if sc["kind"] == "flood_red":
            for c in sc["cut_off"]:
                out[c["village"]] = max(out.get(c["village"], 0), c["population"])
    return out


def _short(name: str) -> str:
    return name.split("] ", 1)[-1]


def render_markdown(report: dict) -> str:
    meta, towns, scenarios = report["meta"], report["townships"], report["scenarios"]
    total_pop = sum(t["population"] for t in towns)
    total_cap = sum(t["capacity"] for t in towns)
    by_town: dict[str, dict[str, dict]] = {}
    for sc in scenarios:
        by_town.setdefault(sc["township"], {})[sc["kind"]] = sc
    ratios = meta["evacuation_ratios"]

    out = [
        "# 宜花東避難收容容量缺口報告",
        "",
        f"產生時間 {meta['generated_at']}。由 `tools/capacity_report.py` 產生，同樣的資料一定得到同樣的報告。",
        "",
        "> [!IMPORTANT]",
        "> 這是**規劃參考**，不是預測，也不能當成災時指揮依據。人口是戶籍人口，避難所容量照登記的算，疏散比例是假設。",
        "",
        "## 資料",
        "",
        "| 項目 | 版本 |",
        "|---|---|",
        f"| 避難所 | 消防署「避難收容處所點位檔」{meta['shelter_dataset_updated']} 版，{meta['shelters']} 處 |",
        f"| 人口 | 內政部戶籍人口（{meta['population_month']}），{meta['villages']} 個村里 |",
        f"| 道路 | OpenStreetMap，匯入 {meta['roads_loaded']} |",
        f"| 土石流潛勢 | 農業部 1753 條土石流潛勢溪流影響範圍，{meta['debris_zones']} 個在宜花東 |",
        "",
        "## 重點",
        "",
        f"- 三縣登記容量合計 {_people(total_cap)} 人，是戶籍人口 {_people(total_pop)} 人的 **{_pct(total_cap / total_pop)}**。"
        f"模擬用的疏散比例是強震 {ratios['earthquake'] * 100:.0f}%、淹水 {ratios['flood'] * 100:.0f}%，"
        "所以只要災害範圍夠大，容量一定不夠，差別只在缺多少、缺在哪裡。",
    ]
    none_eq = no_designated(towns, "earthquake")
    if none_eq:
        names = "、".join(f"{t['township']}（{_people(t['population'])} 人）" for t in none_eq)
        out.append(
            f"- **{len(none_eq)} 個鄉鎮沒有任何一間避難所列為震災適用**：{names}。"
            "這些鄉鎮的避難所在消防署資料裡都沒有勾選震災。這很可能是資料沒填完整，不一定是真的沒有，但地震模擬會因此把這些鄉鎮的人送到鄰近鄉鎮，"
            "最值得先跟公所核對。"
        )
    low = [t for t in towns if (t["hazards"]["earthquake"]["share"] or 0) < LOW_SHARE and t not in none_eq]
    if low:
        names = "、".join(f"{t['township']}（{_pct(t['hazards']['earthquake']['share'])}）"
                         for t in sorted(low, key=lambda t: t["hazards"]["earthquake"]["share"] or 0))
        out.append(f"- 另外有 {len(low)} 個鄉鎮強震時的自有容量不到人口的 {LOW_SHARE * 100:.0f}%：{names}。")
    main = [sc for sc in scenarios if sc["kind"] != "flood_red"]
    if main:
        worst = max(main, key=lambda sc: sc["unplaced"]["1.0"])
        out.append(
            f"- 未安置人數最多的情境是{worst['township']}{KIND_LABELS[worst['kind']]}：{_range(worst)} 人無法安置。"
            f"不管疏散比例怎麼假設，這一圈的避難所最多只夠 {_pct(worst['max_ratio'])} 的受影響人口。"
        )
    reds = [sc for sc in scenarios if sc["kind"] == "flood_red" and sc["cut_off"]]
    if reds:
        cut = cut_off_villages(scenarios)
        out.append(
            f"- 全鄉土石流潛勢溪流發布紅色警戒時，{len(reds)} 個鄉鎮的警戒會讓 {len(cut)} 個村里完全沒有安全道路可到任何避難所，"
            f"合計約 {_people(sum(cut.values()))} 人（依距離加權的受影響人口）。"
        )

    out += [
        "",
        "## 一、各鄉鎮自有容量",
        "",
        "只用資料，沒有假設：鄉鎮內的登記容量占鄉鎮人口幾 %。強震不算沒列為震災避難收容處所的；"
        "淹水不算沒列為水災避難收容處所的，也不算位在土石流潛勢範圍內的。"
        f"跟疏散比例（強震 {ratios['earthquake'] * 100:.0f}%、淹水 {ratios['flood'] * 100:.0f}%）比，就知道自己鄉鎮收不收得下。",
        "",
        "| 縣 | 鄉鎮 | 人口 | 避難所 | 容量 | 強震可用 | 淹水可用 | 適合避難弱者 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for t in towns:
        eq, fl = t["hazards"]["earthquake"], t["hazards"]["flood"]
        out.append(
            f"| {t['county']} | {t['township']} | {_people(t['population'])} | {t['shelters']} | {_people(t['capacity'])} "
            f"| {_people(eq['capacity'])}（{_pct(eq['share'])}） | {_people(fl['capacity'])}（{_pct(fl['share'])}） "
            f"| {_people(t['vulnerable_capacity'])} |"
        )
    zero = [n for t in towns for n in t["zero_capacity"]]
    if zero:
        out += ["", f"登記容量為 0 的避難所（{len(zero)} 處，資料可能有誤）：{'、'.join(_short(n) for n in zero)}。"]

    radius = scenarios[0]["radius_km"] if scenarios else 10
    out += [
        "",
        "## 二、情境模擬",
        "",
        f"每個鄉鎮以人口重心為中心、半徑 {radius:g} 公里，跟地圖上的模擬同一套算法：受影響人口依距離遞減，"
        "每個村里一個起點，沿路網步行，用最小成本流分配（先安置最多人，再讓總時間最短）。"
        "避難所都當成空的。紅色警戒情境是這個鄉鎮所有土石流潛勢溪流同時發布紅色警戒，範圍內的道路封閉。",
        "",
        "「圈內受影響人口」包含鄰近鄉鎮，所以會比鄉鎮人口多。「未安置」是基準疏散比例的人數，括號是比例 ×0.8～×1.2 的範圍。"
        "「最多安置」是所有受影響的人都疏散時能安置的比例，也就是這一圈的避難所最多夠幾 % 的人，不受疏散比例的假設影響。",
        "",
        "| 中心鄉鎮 | 圈內受影響人口 | 強震未安置 | 強震最多安置 | 淹水未安置 | 淹水最多安置 | 紅色警戒被切斷 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for t in towns:
        runs = by_town.get(t["township"], {})
        eq, fl, red = runs.get("earthquake"), runs.get("flood"), runs.get("flood_red")
        covered = (eq or fl or {}).get("covered_population")
        if red is None:
            red_text = "—（無潛勢溪流）"
        elif red["cut_off"]:
            red_text = f"{len(red['cut_off'])} 村里，{_people(sum(c['population'] for c in red['cut_off']))} 人"
        else:
            red_text = "0"
        out.append(
            f"| {t['township']} | {_people(covered) if covered else '—'} "
            f"| {_range(eq) if eq else '—'} | {_max(eq) if eq else '—'} "
            f"| {_range(fl) if fl else '—'} | {_max(fl) if fl else '—'} | {red_text} |"
        )

    cut_rows = [(sc, c) for sc in scenarios if sc["kind"] == "flood_red" for c in sc["cut_off"]]
    if cut_rows:
        out += ["", "### 紅色警戒時被切斷的村里", "", "| 警戒鄉鎮 | 村里 | 受影響人口 |", "|---|---|---:|"]
        for sc, c in sorted(cut_rows, key=lambda r: (-r[1]["population"], r[1]["village"]))[:TOP * 2]:
            out.append(f"| {sc['township']} | {c['village']} | {_people(c['population'])} |")

    usage = report["shelter_usage"]
    full = sorted((n for n, u in usage.items() if u["full"]), key=lambda n: (-usage[n]["full"], n))[:TOP]
    unused = sorted((n for n, u in usage.items() if u["unused"] and not u["used"]), key=lambda n: (-usage[n]["unused"], n))
    out += [
        "",
        "## 三、避難所",
        "",
        f"看強震與淹水共 {len(main)} 個情境的基準分配。",
        "",
    ]
    if full:
        listed = "、".join(f"{_short(n)}（{usage[n]['full']}）" for n in full)
        out += [f"**最常滿載**（前 {len(full)} 間，括號是滿載的情境數）：{listed}。", ""]
    if unused:
        out += [
            f"**是目的地卻從來沒分到人**（{len(unused)} 間）：{'、'.join(_short(n) for n in unused[:TOP * 2])}"
            f"{'等' if len(unused) > TOP * 2 else ''}。"
            "通常是附近的需求已經被更近的避難所接走，或是到得了它的村里都已經安置完；不一定代表它沒有用。",
            "",
        ]

    out += ["## 需要跟公所核對的資料", ""]
    for kind in DESIGNATED_HAZARD:
        missing = no_designated(towns, kind)
        if missing:
            label = DESIGNATED_HAZARD[kind]
            out.append(f"- 沒有任何避難所列為{label}適用：{'、'.join(t['township'] for t in missing)}。")
    tiny = [t for t in towns if t["population"] >= 50000 and t["capacity"] / t["population"] < 0.01]
    if tiny:
        out.append(
            "- 人口多、登記容量卻很少："
            + "、".join(f"{t['township']}（{_people(t['population'])} 人，{t['shelters']} 處共 {_people(t['capacity'])} 人）" for t in tiny)
            + "。可能是學校、體育館這類大型場所沒有列進開放資料。"
        )
    if zero:
        out.append(f"- 登記容量為 0 的 {len(zero)} 處（見上方）。")
    garbled = sorted({_short(n) for n in usage if "?" in n} | {n for t in towns for n in t["zero_capacity"] if "?" in n})
    if garbled:
        out.append("- 名稱裡的「?」是來源資料的缺字（例如「土?天主堂」應該是土坂），不影響計算。")
    out += [
        "",
        "## 限制",
        "",
        "- 人口是戶籍人口，不是當下實際在那裡的人（觀光客、通勤、外地工作都沒算）。",
        "- 疏散比例是固定的假設，同一種災害每個鄉鎮都一樣；實際比例跟災害強度、房屋受損、季節都有關。",
        "- 每個村里只有一個起點（最大的聚落），通行時間只能當量級看。",
        "- 容量照登記的算，沒有扣掉受損或不能用的空間；敏感度分析見 docs/evaluation.md。",
        "- 紅色警戒情境假設整個鄉鎮的潛勢溪流同時發布紅色警戒，比實際常見的情況嚴重。",
    ]
    return "\n".join(out) + "\n"
