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
