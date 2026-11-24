"""
英文問句 → 規則層看得懂的中文關鍵字（英文模式第二階段）。

路由與篩選的規則（query_rules、intent、alert_answer、地名）全部是中文關鍵字。
與其在每張表旁邊各維護一份英文表，這裡在 build_context 一開頭把英文說法換成那些表裡的中文詞，
後面整條路照舊：「shelters in Luodong with more than 500 people」→「避難所羅東鎮超過500人」。

- 只改寫以英文為主的問句（拉丁字母比中文字多），中文問句原封不動，不會因為這層改變任何行為
- 認得的片語換成中文；剩下的英文功能詞（the、is、where…）拿掉；其餘英文詞原樣保留，
  例如不在任何清單裡的小地名（Zhiben），交給後面「找不到這個地方」那條路誠實回答
- 送給模型的問題仍是使用者原本的英文，改寫結果只給規則與檢索用
- 避難所名稱裡的英文（F Hotel）不改寫
"""
import re

from services.web.i18n import COUNTY_EN, OUT_OF_SCOPE_EN, REGION_EN, TOWNSHIP_EN

_LATIN = re.compile(r"[A-Za-z]")
_CJK = re.compile(r"[一-鿿]")

# 鄉鎮常見的其他拼法（舊式拼音、少了撇號）
TOWNSHIP_VARIANTS = {
    "羅東鎮": ["lotung"], "蘇澳鎮": ["suao"], "礁溪鄉": ["jiaosi", "chiaohsi"], "吉安鄉": ["jian"],
    "南澳鄉": ["nanao"], "成功鎮": ["chengkung"], "池上鄉": ["chihshang"], "綠島鄉": ["ludao", "green island"],
    "蘭嶼鄉": ["lanyu", "orchid island"], "花蓮市": ["hualien city"], "台東市": ["taitung city"],
    "太麻里鄉": ["taimali"], "卑南鄉": ["peinan"], "壽豐鄉": ["shoufeng"], "瑞穗鄉": ["ruisui"],
}


# (英文片語的正規表示式, 換成的中文)。由上而下比對，長的、具體的放前面：
# 「debris flow alert」要先於「debris flow」與「alert」
PHRASES = [
    # 座標
    (r"lat(?:itude)?\s*[:=]?\s*(\d{2}\.\d+)\s*,?\s*(?:lon|lng|long|longitude)\s*[:=]?\s*(\d{3}\.\d+)", r"緯度\1 經度\2"),
    (r"(?:lon|lng|long|longitude)\s*[:=]?\s*(\d{3}\.\d+)\s*,?\s*lat(?:itude)?\s*[:=]?\s*(\d{2}\.\d+)", r"經度\1 緯度\2"),
    # 即時警戒、警報與分級
    (r"red (?:debris[- ]flow )?alerts?", "紅色警戒"), (r"yellow (?:debris[- ]flow )?alerts?", "黃色警戒"),
    (r"debris[- ]flow (?:alerts?|warnings?)", "土石流警戒"),
    (r"sea warnings?", "海警"), (r"land warnings?", "陸警"),
    (r"typhoon warnings?", "颱風警報"), (r"tsunami warnings?", "海嘯警報"),
    (r"alerts?", "警戒"), (r"warnings?", "警報"),
    (r"(?:seismic )?intensity", "震度"), (r"levels?|categor(?:y|ies)|grades?", "等級"),
    (r"right now|currently|current|at the moment|in effect|active|today|now", "現在"),
    (r"are there any|is there any|are there|is there|any", "有沒有"),
    # 即時消息（系統沒有，指向中央氣象署）
    (r"latest news|news|latest updates?|live updates?|updates? on", "最新消息"), (r"latest", "最新"),
    (r"road conditions?|road closures?|roads? closed|traffic conditions?", "路況"),
    (r"typhoon (?:track|path|route)|where is the typhoon", "颱風路徑"),
    (r"rainfall amounts?|rainfall", "雨量"), (r"casualties|damage reports?", "災情"),
    # 資料本身
    (r"newly added|recently added|new shelters?|added", "新增避難所"),
    (r"last updated?|updated|update", "更新"),
    (r"data sources?|where does (?:the |this |your )?(?:[a-z]+ )?data come from|source of (?:the |this |your )?(?:[a-z]+ )?data",
     "資料來源"),
    (r"data", "資料"),
    # 模擬與疏散
    (r"simulation results?", "模擬結果"),
    (r"which shelters (?:are|were) affected|affected shelters?", "受影響的避難所"),
    (r"simulations?|simulated", "模擬"),
    (r"affected", "受影響"),
    (r"excluded|exclude|left out", "排除"),
    (r"allocat(?:ed|ion)|assign(?:ed|ment)|distributed", "分配"),
    (r"unplaced|not (?:be )?(?:placed|accommodated|housed)|without a place|no room for", "沒有被安置"),
    (r"shortfall|gap", "缺口"), (r"enough", "夠不夠"),
    (r"bottlenecks?|congest(?:ed|ion)|traffic jams?|jammed|jam", "瓶頸"),
    (r"(?:to )?clear (?:the area|out)|cleared", "清空"),
    (r"evacuation (?:advice|plan)|how (?:should|do|can) (?:we|i|people) evacuate|how to evacuate", "疏散建議"),
    (r"advice|recommendations?|suggestions?", "建議"),
    # 去哪、住哪（避難所的線索，也讓「颱風時要去哪」不被當成知識題）
    (r"where (?:can|should|do|could|would) (?:i|we|you|people) (?:go|stay|evacuate to|head)", "要去哪裡"),
    (r"spend the night|overnight|stay|sleep", "過夜"),
    # 防災知識：問做法、問意思
    (r"what (?:should|do|can|must) (?:i|we|you|people) do|what to do"
     r"|how (?:should|do|can) (?:i|we|you) (?:prepare|protect|stay safe)", "怎麼辦"),
    (r"(?:emergency|go)[- ]?(?:kit|bag)s?|supplies|prepare|preparations?|get ready", "準備"),
    (r"what is meant by|meaning of|means|meaning|mean", "什麼意思"),
    (r"differences?|differ", "差別"),
    (r"warning signs?|signs", "前兆"),
    (r"(?:safe )?to (?:go|return) (?:back )?home|go back home", "可以回家"),
    (r"should i|should we|do i need to|do we need to|must i|is it safe to|can i still", "該怎麼做"),
    (r"when", "什麼時候"), (r"why", "為什麼"),
    # 災害
    (r"storm surges?|seawater flooding", "海水倒灌"),
    (r"debris[- ]flows?|mud ?slides?|mud ?flows?", "土石流"), (r"landslides?|rockfalls?", "崩塌"),
    (r"aftershocks?", "餘震"), (r"earthquakes?|quakes?|tremors?", "地震"),
    (r"tsunamis?", "海嘯"), (r"typhoons?", "颱風"),
    (r"heavy rain(?:fall)?|torrential rain|downpours?", "豪雨"),
    (r"flood(?:s|ing|ed)?|inundat(?:ed|ion)", "淹水"),
    (r"power outages?|blackouts?", "停電"), (r"fires?", "火災"),
    # 離題
    (r"school closures?|work (?:and|or) school (?:closures?|cancell?ations?)|days? off|cancell?ed (?:work|school|classes)", "停班停課"),
    (r"weather|forecast", "天氣"), (r"temperatures?", "氣溫"), (r"rain(?:ing|s)?", "下雨"),
    (r"subsid(?:y|ies)|relief (?:payments?|money)|financial aid|allowances?", "補助"),
    (r"insurance|insurance claims?", "保險"),
    (r"hospitals?", "醫院"), (r"clinics?", "診所"), (r"pharmac(?:y|ies)|drugstores?", "藥局"),
    (r"gas stations?|petrol stations?", "加油站"),
    (r"convenience stores?|7-?eleven|seven-?eleven", "便利商店"),
    (r"parking(?: lots?)?|car parks?", "停車場"), (r"post offices?", "郵局"), (r"banks?|atms?", "銀行"),
    (r"restaurants?", "餐廳"), (r"b ?& ?bs?|homestays?|guesthouses?", "民宿"),
    (r"bus(?:es)?", "公車"), (r"trains?", "火車"), (r"airports?", "機場"),
    # 容量條件（數字先拿掉千分位逗號，見 rewrite）
    (r"(?:more than|over|above|greater than|exceeding) (\d{2,6})(?: people| persons)?", r"超過\1人"),
    (r"at least (\d{2,6})(?: people| persons)?|(\d{2,6})\+|(\d{2,6}) (?:people |persons )?or more", r"\1\2\3人以上"),
    (r"(?:at most|up to|no more than) (\d{2,6})(?: people| persons)?|(\d{2,6}) (?:people |persons )?or (?:fewer|less)",
     r"\1\2人以下"),
    (r"(?:fewer than|less than|under|below) (\d{2,6})(?: people| persons)?", r"少於\1人"),
    (r"(?:a |one )?thousands? (?:of )?(?:people|persons)|1000 people", "千人"),
    (r"largest|biggest|most capacity|highest capacity|holds? the most|most people|most spacious", "最大"),
    (r"smallest|lowest capacity|fewest people", "最小"),
    (r"rank(?:ed|ing)?(?: by capacity)?|sorted by capacity", "容量排名"),
    (r"free spaces?|vacanc(?:y|ies)|available (?:spaces?|spots?|beds?|places?)|spaces? left|room left|spots? left", "空位"),
    (r"how many people|capacity|accommodate|hold", "容量"),
    (r"medium(?:-sized)?", "中型"), (r"large", "大型"), (r"small", "小型"),
    (r"indoors?|covered|sheltered from the rain", "室內"),
    # 「seniors」不能比到「Senior High School」
    (r"wheelchair(?:[- ]accessible)?|accessible|accessibility|elderly|seniors?(?! high)|older people|disabled|disabilit(?:y|ies)|"
     r"limited mobility|pregnant", "無障礙"),
    # 設施
    (r"(?:elementary|primary) schools?", "國小"), (r"(?:junior high|middle) schools?|junior highs?", "國中"),
    (r"(?:senior )?high schools?|senior highs?", "高中"), (r"universit(?:y|ies)|colleges?", "大學"),
    (r"kindergartens?|preschools?|nurser(?:y|ies)", "幼兒園"), (r"schools?", "學校"),
    (r"gym(?:nasium)?s?|stadiums?|sports? (?:centers?|centres?|venues?|fields?|halls?)|arenas?", "體育館"),
    (r"librar(?:y|ies)", "圖書館"),
    # 「Taitung City Office」的「Taitung City」先被換成台東市，剩下的「Office」單獨算公所
    (r"(?:township|town|city|district) (?:offices?|halls?)|county government|offices?", "公所"),
    (r"(?:community|activity|activities) (?:centers?|centres?)", "活動中心"),
    (r"temples?|shrines?", "廟"), (r"church(?:es)?", "教會"), (r"hotels?", "旅館"),
    # 「hall」不換成會館：展演廳、禮堂、文化館都可能叫 hall，篩錯類別會把正確答案擋掉，不篩還有語意檢索
    (r"military (?:camps?|bases?)|barracks", "營區"),
    # 避難所本身與疏散
    (r"evacuation (?:shelters?|cent(?:er|re)s?|sites?)|emergency shelters?|shelters?|refuges?", "避難所"),
    (r"evacuat(?:e|ing)|flee|escape", "撤離"), (r"evacuation", "疏散"),
    (r"safe|safety", "安全"), (r"danger(?:ous)?", "危險"),
    # 距離：「near me」要座標；「near 知本」「near Zhiben」換成「X附近」
    (r"(?:nearest|closest) (?:to|from) me|near(?:est)? me|close to me|around me|near my location", "離我最近"),
    (r"nearest|closest", "最近"), (r"nearby|near here|around here|close by|near|around|close to", "附近"),
    (r"which|what", "哪些"), (r"where", "哪裡"),
]

# 拿掉的英文功能詞：留下來的話「離我最近的避難所」剝完關鍵字還剩一串英文字，會被當成使用者說了地點
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "am", "do", "does", "did", "can", "could", "will",
    "would", "should", "may", "might", "must", "shall", "i", "me", "my", "we", "us", "our", "you", "your", "it",
    "its", "they", "them", "their", "this", "that", "these", "those", "there", "here", "of", "in", "on", "at", "to",
    "for", "from", "by", "with", "about", "into", "and", "or", "but", "if", "so", "as", "than", "then", "please",
    "tell", "show", "list", "give", "find", "know", "want", "need", "like", "get", "go", "going", "located",
    "location", "place", "places", "area", "areas", "one", "ones", "some", "all", "every", "each", "many", "much",
    "how", "who", "whom", "whose", "has", "have", "had", "having", "not", "no", "yes", "ok", "okay", "hi", "hello",
    "thanks", "thank", "people", "person", "persons", "kind", "kinds", "type", "types",
    "during", "before", "after", "while", "within", "inside", "outside", "recently", "tomorrow", "yesterday",
    "tonight", "somewhere", "anywhere", "else", "other", "road", "roads", "street", "streets", "up", "out",
    "take", "taking", "along",
    "county", "city", "township", "town", "district", "taiwan", "eastern", "east", "also", "just", "still", "only",
    "very", "more", "most", "less", "best", "good", "use", "used", "help", "let", "lets", "s", "t", "re", "ll", "ve",
}

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'&-]*")
# 句首大寫的這些字不是路名的一部分：「Near Jieren Street」「Shelters on Linsen Road」
_BEFORE_ROAD = {"near", "around", "on", "along", "by", "at", "in", "shelter", "shelters", "nearest", "closest"}
_ROAD_EN = re.compile(
    r"\b((?:[A-Z][a-z]+|\d+(?:st|nd|rd|th))(?:[ -](?:[A-Z][a-z]+|\d+(?:st|nd|rd|th)))*)\s+"
    r"(Road|Rd|Street|St|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Alley)\b\.?(?:,?\s*Sec(?:tion|\.)?\s*\d+)?"
)
_NEAR_BEFORE = re.compile(
    r"\b(?:near|around|close to|(?:nearest|closest)\s+(?:shelters?\s+)?(?:to|from))\s+([一-鿿]{2,12}|[A-Z][A-Za-z'-]{1,30})"
)


def _place_phrases() -> list[tuple[str, str]]:
    """縣、地區、鄉鎮的英文名稱 → 中文；全名先比（Hualien City 要先於 Hualien）"""
    out = []
    for zh, en in TOWNSHIP_EN.items():
        out.append((en.lower(), zh))
        bare = en.rsplit(" ", 1)[0].lower()
        if bare not in {r.lower() for r in REGION_EN.values()}:
            out.append((bare, zh))
        for variant in TOWNSHIP_VARIANTS.get(zh, []):
            out.append((variant, zh))
    out += [(en.lower(), zh) for zh, en in COUNTY_EN.items() if zh != "臺東縣"]
    out += [(en.lower(), zh) for zh, en in REGION_EN.items()]
    # 宜蘭的舊拼法：國立宜蘭大學的英文校名是 National Ilan University
    out += [("ilan", "宜蘭"), ("taidong", "台東")]
    # 範圍外縣市：同一個英文名稱有「台」「臺」兩種中文寫法，取「台」那個
    out += [(en.lower(), zh) for zh, en in OUT_OF_SCOPE_EN.items() if not zh.startswith("臺")]
    return sorted(out, key=lambda pair: len(pair[0]), reverse=True)


def _compile(pairs):
    # 選項要包在一組裡，字邊界才會套到每一個選項：少了這層，「any」會比到「many」的後半
    return [(re.compile(rf"(?<![A-Za-z])(?:{pattern})(?![A-Za-z])", re.IGNORECASE), zh) for pattern, zh in pairs]


_PLACES = _compile([(re.escape(en).replace(r"\ ", r"\s+").replace("'", "'?"), zh) for en, zh in _place_phrases()])
_PHRASES = _compile(PHRASES)
_PLACE_WORDS = {en for en, _ in _place_phrases()}


def is_english(message: str) -> bool:
    """以英文為主：拉丁字母比中文字多"""
    return len(_LATIN.findall(message or "")) > len(_CJK.findall(message or ""))


def rewrite(message: str, protected: list[str] | None = None) -> str:
    """
    英文問句 → 規則層用的中文關鍵字句；不是以英文為主的問句原樣回傳。
    protected：避難所名稱裡帶英文的（F Hotel），原樣保留
    """
    if not message or not is_english(message):
        return message
    # 撇號統一，縮寫展開：「what's」「where's」的 's 不留下來
    text = message.replace("’", "'")
    text = re.sub(r"(?i)(?<=[a-z])'s(?![a-z])", " is", text)
    text = re.sub(r"(?i)(?<=[a-z])n't(?![a-z])", " not", text)
    text = re.sub(r"(?i)(?<=[a-z])'(?:re|ll|ve|d|m)(?![a-z])", "", text)
    kept = {}
    for name in protected or []:
        if _LATIN.search(name) and name.lower() in text.lower():
            text = re.sub(re.escape(name), lambda _m, name=name: _keep(kept, name), text, flags=re.IGNORECASE)
    # 「shelters near 知本」→「知本附近 避難所」：規則層認的是「X附近」的語序，而且要放在句首——
    # 接在「避難所」後面的話，地名索引會把「所知本」看成一個更長名稱的一部分
    near = []

    def _take_near(m: re.Match) -> str:
        place = m.group(1)
        for pattern, zh in _PLACES:
            place = pattern.sub(zh, place)
        near.append(place + "附近")
        return " "

    def _take_road(m: re.Match) -> str:
        words = m.group(1).split()
        while words and words[0].lower() in STOPWORDS | _BEFORE_ROAD:
            words.pop(0)
        if not words:
            return m.group(0)
        name = " ".join(words)
        # 路名本身是鄉鎮（Dawu Street）：交給鄉鎮篩選，至少找得到那個鄉鎮的避難所
        if name.lower() in _PLACE_WORDS:
            return m.group(0)
        near.append(f"{name} {m.group(2)}附近")
        return " "

    # 英文路名（Linsen Road、Cihui 3rd Street）：資料裡的路名只有中文，改寫不了。
    # 評測裡這類題目的向量距離（0.45～0.63）跟答對的題目混在一起，門檻擋不住，交給語意檢索
    # 只會拿別處的避難所來答，所以換成「X附近」，讓「找不到這個地方」那條路誠實回答
    text = _ROAD_EN.sub(_take_road, text)
    text = _NEAR_BEFORE.sub(_take_near, text)
    # 問句裡本來就寫的中文（「Where is 頭城國中?」）跟改寫出來的關鍵字之間留一個空白：
    # 黏成「哪裡頭城國中」的話，點名比對會抽到「裡頭城國中」，回答「沒有裡頭城國中這間」
    text = _ORIGINAL_CJK.sub(lambda m: _keep(kept, m.group(0)), text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    for pattern, zh in _PLACES + _PHRASES:
        text = pattern.sub(lambda m, zh=zh: " " + m.expand(zh) + " ", text)
    text = _TOKEN.sub(lambda m: "" if m.group(0).lower().strip("'") in STOPWORDS else m.group(0), text)
    text = " ".join(near + [text])
    # 改寫出來的中文彼此之間的空白拿掉，規則層才比得到「X附近」「超過500人」這種連在一起的寫法
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[^\sA-Za-z0-9\x00]) (?=[^\sA-Za-z0-9\x00])", "", text)
    text = re.sub(r"(?<=[一-鿿]) (?=[\d])|(?<=\d) (?=[一-鿿])", "", text)
    for key, original in kept.items():
        text = text.replace(key, f" {original} ")
    return re.sub(r"\s+", " ", text).strip()


# 原句裡的中文片段，連同夾在中間的數字（國軍805醫院）算一段
_ORIGINAL_CJK = re.compile(r"[一-鿿0-9（）()]*[一-鿿][一-鿿0-9（）()]*")


def _keep(kept: dict, original: str) -> str:
    key = f"\x00{len(kept)}\x00"
    kept[key] = original
    return key
