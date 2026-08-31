class Shelter:
    def __init__(self, name: str, total_vessel: int, lat: float, lon: float, total_people: int = 0):
        self.name = name
        self.total_vessel = total_vessel
        self.total_people = total_people
        self.lat = lat
        self.lon = lon

    @property
    def occupancy_rate(self) -> float:
        if self.total_vessel == 0:
            return 0.0
        return (self.total_people / self.total_vessel) * 100