import unittest

from models.shelter import Shelter
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

if __name__ == '__main__':
    unittest.main()
