from dataclasses import dataclass


@dataclass
class Shelter:
    name: str
    capacity: int
    lat: float
    lon: float
    current_people: int = 0
    address: str = ""
    # 以下來自消防署避難收容處所點位檔；舊資料或測試沒有這些欄位時保持預設值
    township: str = ""
    village: str = ""
    # 適用災害類別（水災、震災、土石流、海嘯）；空的代表來源沒填，不代表都不適用
    hazards: tuple[str, ...] = ()
    # 室內 / 室外 / 適合避難弱者安置；None 是來源沒填
    indoor: bool | None = None
    outdoor: bool | None = None
    vulnerable_ok: bool | None = None
    phone: str = ""
    # 預計收容村里
    serves: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.capacity - self.current_people)

    @property
    def occupancy_rate(self) -> float:
        if self.capacity == 0:
            return 0.0
        return (self.current_people / self.capacity) * 100


@dataclass
class NearbyShelter:
    """
    避難所加上「這一次查詢」算出的距離。

    距離是查詢結果而不是避難所的屬性，所以不塞進 Shelter——否則大部分 Shelter 實例身上
    會有一個 None 的 distance_km，任何拿它來排序的人都會踩到。
    """
    shelter: Shelter
    distance_km: float
    # 沿路網的距離；路網算不出來（沒有 pgRouting、離路太遠、路網不連通）時是 None，只剩直線距離
    road_km: float | None = None
