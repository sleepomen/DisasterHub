"""
領域物件 → 前端 / API 的 JSON。

repository 一律回 Shelter（或 NearbyShelter），dict 只在這一層產生：
欄位名稱是對外契約（前端與測試都綁著它），集中在一個地方才改得安全。
注意 current_people → current_ppl 的改名也只在這裡發生。
"""


class MapService:
    @staticmethod
    def _shelter_dict(s) -> dict:
        return {
            "name": s.name,
            "capacity": s.capacity,
            "current_ppl": s.current_people,
            "remaining": s.remaining,
            "lat": s.lat,
            "lon": s.lon,
            "address": s.address,
        }

    def to_map_points(self, shelters) -> list[dict]:
        """地圖標記：畫點與負載的欄位刻意比較短；其餘是 popup 顯示的消防署細節"""
        return [
            {
                "name": s.name,
                "lat": s.lat,
                "lon": s.lon,
                "z": s.capacity,
                "ppl": s.current_people,
                "address": s.address,
                "hazards": list(s.hazards),
                "indoor": s.indoor,
                "outdoor": s.outdoor,
                "vulnerable_ok": s.vulnerable_ok,
                "phone": s.phone,
            }
            for s in shelters
        ]

    def to_impacted(self, shelters) -> list[dict]:
        """/api/simulate_disaster 的受影響清單；前端的疏散動畫靠 remaining / lat / lon 決定人往哪走"""
        return [self._shelter_dict(s) for s in shelters]

    def to_nearest(self, nearby) -> list[dict]:
        """
        /api/nearest_shelter：在避難所欄位之外附上這次查詢算出的距離（直線，以及算得出來時的道路距離）。
        unreachable：路線被警戒封閉的道路擋住；hazard_m：路線仍經過警戒範圍的長度
        """
        return [
            {
                **self._shelter_dict(n.shelter), "distance_km": n.distance_km, "road_distance_km": n.road_km,
                "unreachable": n.unreachable, "hazard_m": n.hazard_m,
            }
            for n in nearby
        ]
