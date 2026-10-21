import json
import pathlib
import sys
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_hazard_zones import signed_area, to_multipolygon, twd97_to_wgs84  # noqa: E402

GEOJSON = ROOT / "data_reference" / "debris_flow_east.geojson"


def test_twd97_inverse_matches_postgis():
    # 對照值是 PostGIS ST_Transform(ST_SetSRID(ST_Point(x, y), 3826), 4326) 的結果
    for (x, y), (lon, lat) in {
        (313869, 2727295): (121.63096441, 24.65123221),
        (250000, 2650000): (121.00000000, 23.95464317),
        (300000, 2600000): (121.48957288, 23.50237911),
    }.items():
        got_lon, got_lat = twd97_to_wgs84(x, y)
        assert abs(got_lon - lon) < 1e-7 and abs(got_lat - lat) < 1e-7


def test_rings_become_counterclockwise_shells_with_holes_attached():
    shell = [(300000, 2600000), (300000, 2601000), (301000, 2601000), (301000, 2600000), (300000, 2600000)]  # 順時針
    hole = shell[::-1]                                                                                      # 逆時針
    assert signed_area(shell) < 0 < signed_area(hole)
    polygons = to_multipolygon([shell, hole])
    assert len(polygons) == 1 and len(polygons[0]) == 2  # 一個多邊形，帶一個洞


def test_bundled_geojson_covers_the_three_east_counties():
    data = json.loads(GEOJSON.read_text(encoding="utf-8"))
    counties = {f["properties"]["county"] for f in data["features"]}
    assert counties == {"宜蘭縣", "花蓮縣", "台東縣"}
    assert data["source"]["provider"] == "農業部"
    # 座標要落在東部：經度 120.7～122、緯度 21.8～25.1，投影算錯會整批跑到別處
    for f in data["features"][:50]:
        lon, lat = f["geometry"]["coordinates"][0][0][0]
        assert 120.7 < lon < 122.1 and 21.8 < lat < 25.1


def test_hazard_sync_failure_never_breaks_shelter_sync():
    from services.sync_service import DataSyncService

    svc = DataSyncService.__new__(DataSyncService)
    svc.repository = MagicMock()
    svc.repository.replace_hazard_zones.side_effect = RuntimeError("db down")
    assert svc.sync_hazard_zones() == {}

    svc.repository.replace_hazard_zones.side_effect = None
    svc.repository.replace_hazard_zones.return_value = 531
    assert svc.sync_hazard_zones() == {"debris_flow": 531}


def test_hazard_distances_group_by_shelter_nearest_first():
    from contextlib import contextmanager
    from unittest.mock import patch

    from repositories.shelter_repository import ShelterRepository

    cursor = MagicMock()
    cursor.fetchall.return_value = [
        ("[YILAN] 南山國小", "宜縣DF079", 121.4),
        ("[YILAN] 南山國小", "宜縣DF078", 180.2),
        ("[YILAN] 澳花國小", "宜縣DF139", 0),
    ]

    @contextmanager
    def fake_cursor(self):
        yield cursor

    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        hits = ShelterRepository().shelter_hazard_distances("debris_flow", 200)
    assert hits == {
        "[YILAN] 南山國小": [("宜縣DF079", 121.4), ("宜縣DF078", 180.2)],
        "[YILAN] 澳花國小": [("宜縣DF139", 0.0)],
    }
    assert cursor.execute.call_args.args[1] == ("debris_flow", 200)
