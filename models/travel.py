"""
交通方式與速度：路線依「時間」排序用。

步行：所有道路一樣快，所以步行最快就是步行最近，排序跟道路距離一樣。
速度用 1.0 m/s：災害疏散要算進長者、小孩、攜帶物品的人，日本內閣府的津波避難計畫也是用這個值估算，
比一般成人的 1.3 m/s 保守。

開車：依 OSM 的道路等級給速度（公里／小時）。數字是台灣的速限加上會車、路口的折減，
沒有即時路況，也沒有算疏散時的壅塞——同一時間大家都往外開，實際只會更慢。
走到路上、離開道路的那兩小段一律用步行速度
"""
TRAVEL_MODES = ("walk", "drive")
DEFAULT_MODE = "walk"

WALK_MPS = 1.0

DRIVE_KMH = {
    "motorway": 80, "motorway_link": 50,
    "trunk": 60, "trunk_link": 40,
    "primary": 50, "primary_link": 35,
    "secondary": 40, "secondary_link": 30,
    "tertiary": 35, "tertiary_link": 25,
    "unclassified": 25, "residential": 20,
    "living_street": 10, "service": 15, "track": 10,
}
DEFAULT_DRIVE_KMH = 20
# 算不出路線、只能用直線距離估算時的平均車速（含繞路已經由 DETOUR_FACTOR 乘進距離）
FALLBACK_DRIVE_KMH = 30


def normalize_mode(mode: str | None) -> str:
    return mode if mode in TRAVEL_MODES else DEFAULT_MODE


def fallback_seconds(meters: float, mode: str) -> float:
    """沒有路線時，用估算的道路距離換成時間"""
    speed = WALK_MPS if mode == "walk" else FALLBACK_DRIVE_KMH / 3.6
    return meters / speed


def minutes(seconds: float | None) -> int | None:
    """顯示用的分鐘數，至少 1 分鐘"""
    return None if seconds is None else max(1, round(seconds / 60))
