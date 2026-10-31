"""
英文模式（第一階段：回答用英文，問題仍以中文最準）。

避難所名稱一律保留中文：消防署資料沒有英文名稱，自己翻或轉拼音都不可靠，
使用者到現場看到的招牌也是中文。鄉鎮、縣市、設施類型、災害類別翻成英文，
鄉鎮用各鄉鎮市公所的官方英文名稱
"""
LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

REGION_EN = {"宜蘭": "Yilan", "花蓮": "Hualien", "台東": "Taitung"}
COUNTY_EN = {"宜蘭縣": "Yilan County", "花蓮縣": "Hualien County", "台東縣": "Taitung County", "臺東縣": "Taitung County"}

TOWNSHIP_EN = {
    # 宜蘭
    "宜蘭市": "Yilan City", "羅東鎮": "Luodong Township", "蘇澳鎮": "Su'ao Township", "頭城鎮": "Toucheng Township",
    "礁溪鄉": "Jiaoxi Township", "壯圍鄉": "Zhuangwei Township", "員山鄉": "Yuanshan Township",
    "冬山鄉": "Dongshan Township", "五結鄉": "Wujie Township", "三星鄉": "Sanxing Township",
    "大同鄉": "Datong Township", "南澳鄉": "Nan'ao Township",
    # 花蓮
    "花蓮市": "Hualien City", "鳳林鎮": "Fenglin Township", "玉里鎮": "Yuli Township", "新城鄉": "Xincheng Township",
    "吉安鄉": "Ji'an Township", "壽豐鄉": "Shoufeng Township", "光復鄉": "Guangfu Township",
    "豐濱鄉": "Fengbin Township", "瑞穗鄉": "Ruisui Township", "富里鄉": "Fuli Township",
    "秀林鄉": "Xiulin Township", "萬榮鄉": "Wanrong Township", "卓溪鄉": "Zhuoxi Township",
    # 台東
    "台東市": "Taitung City", "成功鎮": "Chenggong Township", "關山鎮": "Guanshan Township",
    "卑南鄉": "Beinan Township", "大武鄉": "Dawu Township", "太麻里鄉": "Taimali Township",
    "東河鄉": "Donghe Township", "長濱鄉": "Changbin Township", "鹿野鄉": "Luye Township",
    "池上鄉": "Chishang Township", "綠島鄉": "Green Island Township", "延平鄉": "Yanping Township",
    "海端鄉": "Haiduan Township", "達仁鄉": "Daren Township", "金峰鄉": "Jinfeng Township",
    "蘭嶼鄉": "Orchid Island Township",
}

FACILITY_EN = {
    "國小": "elementary schools", "國中": "junior high schools", "高中": "senior high schools",
    "大學": "universities", "幼兒園": "kindergartens", "公所": "township offices", "圖書館": "libraries",
    "宗教場所": "religious sites", "旅館": "hotels", "會館": "halls", "活動中心": "community centers",
    "社福機構": "social welfare facilities", "軍事營區": "military camps", "體育場館": "sports venues",
    "其他": "other",
}
HAZARD_EN = {"水災": "floods", "震災": "earthquakes", "土石流": "debris flows", "海嘯": "tsunamis"}
SIZE_EN = {"大型": "large", "中型": "medium-sized", "小型": "small"}


def place_en(name: str) -> str:
    """鄉鎮、縣、地區 → 英文；不認得就原樣回傳"""
    name = (name or "").replace("臺", "台")
    return TOWNSHIP_EN.get(name) or COUNTY_EN.get(name) or REGION_EN.get(name) or name


def shelters_en(n: int) -> str:
    """「1 shelter」「3 shelters」"""
    return f"{n} shelter{'' if n == 1 else 's'}"


def normalize_lang(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT_LANG
