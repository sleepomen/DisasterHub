import unittest

from models.shelter import NearbyShelter, Shelter
from services.map_service import MapService


class TestMapService(unittest.TestCase):
    def test_transform_shelters_to_map_points(self):
        # Arrange
        mock_shelters = [
            Shelter(name="花蓮體育館", capacity=100, lat=23.9, lon=121.6, current_people=10),
            Shelter(name="宜蘭國小", capacity=200, lat=24.7, lon=121.7, current_people=50)
        ]
        service = MapService()

        # Act
        result = service.to_map_points(mock_shelters)

        # Assert
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], "花蓮體育館")
        self.assertEqual(result[0]['z'], 100)
        self.assertEqual(result[0]['ppl'], 10)
        self.assertEqual(result[1]['lat'], 24.7)

    def test_output_keys(self):
        # 驗證輸出格式包含所有前端需要的欄位
        mock_shelters = [
            Shelter(name="測試", capacity=500, lat=23.9, lon=121.6, current_people=0)
        ]
        service = MapService()
        result = service.to_map_points(mock_shelters)

        self.assertIn('name', result[0])
        self.assertIn('lat', result[0])
        self.assertIn('lon', result[0])
        self.assertIn('z', result[0])
        self.assertIn('ppl', result[0])

    def test_empty_input(self):
        self.assertEqual(MapService().to_map_points([]), [])
        self.assertEqual(MapService().to_impacted([]), [])
        self.assertEqual(MapService().to_nearest([]), [])

    def test_impacted_keys_are_the_api_contract(self):
        # 這些鍵是對外契約：前端的疏散動畫靠 remaining / lat / lon 決定人往哪走，
        # 回寫收容人數靠 name。欄位改名會讓地圖安靜地壞掉，所以在這裡釘住
        shelter = Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6, current_people=10, address="花蓮市")
        row = MapService().to_impacted([shelter])[0]
        self.assertEqual(
            set(row), {"name", "capacity", "current_ppl", "remaining", "lat", "lon", "address"}
        )
        # 領域模型叫 current_people，對外一律是 current_ppl，改名只發生在這一層
        self.assertEqual(row["current_ppl"], 10)
        self.assertEqual(row["remaining"], 90)
        self.assertEqual(row["name"], "[HUALIEN] 甲")

    def test_nearest_adds_distance_to_the_same_fields(self):
        nearby = NearbyShelter(
            shelter=Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7, current_people=0),
            distance_km=1.23,
        )
        row = MapService().to_nearest([nearby])[0]
        self.assertEqual(
            set(row),
            {"name", "capacity", "current_ppl", "remaining", "lat", "lon", "address", "distance_km"},
        )
        self.assertEqual(row["distance_km"], 1.23)
        self.assertEqual(row["remaining"], 300)

if __name__ == '__main__':
    unittest.main()
