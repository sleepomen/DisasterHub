from dataclasses import dataclass


@dataclass
class Shelter:
    name: str
    capacity: int
    lat: float
    lon: float
    current_people: int = 0
    address: str = ""

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
