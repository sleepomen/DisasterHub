class MapService:
    def to_map_points(self, shelters):
        #物件轉dict格式
        return [
            {
                "name": s.name,
                "lat": s.lat,
                "lon": s.lon,
                "z": s.capacity,
                "ppl": s.current_people
            }
            for s in shelters
        ]
