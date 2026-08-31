from models.shelter import Shelter

def test_occupancy_rate_calculation():
    # 容量 100，入住 50 人，負載率應為 50%
    shelter = Shelter(name="測試中心", capacity=100, lat=23.9, lon=121.6, current_people=50)
    assert shelter.occupancy_rate == 50.0

def test_occupancy_rate_full():
    # 滿載時負載率應為 100%
    shelter = Shelter(name="測試中心", capacity=100, lat=23.9, lon=121.6, current_people=100)
    assert shelter.occupancy_rate == 100.0

def test_occupancy_rate_zero_capacity():
    # 容量為 0 時不應報錯，回傳 0
    shelter = Shelter(name="測試中心", capacity=0, lat=23.9, lon=121.6, current_people=0)
    assert shelter.occupancy_rate == 0.0

def test_shelter_default_people():
    # current_people 預設應為 0
    shelter = Shelter(name="測試中心", capacity=500, lat=23.9, lon=121.6)
    assert shelter.current_people == 0

# 以下是新增的
def test_remaining_never_negative():
    shelter = Shelter(name="測試中心", capacity=100, lat=23.9, lon=121.6, current_people=150)
    assert shelter.remaining == 0

def test_remaining_normal():
    shelter = Shelter(name="測試中心", capacity=100, lat=23.9, lon=121.6, current_people=30)
    assert shelter.remaining == 70
