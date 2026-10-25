from services.data_fetcher import DataFetcher


def test_fetcher_load_data():
    # Arrange
    fetcher = DataFetcher()

    # Act
    shelters = fetcher.get_shelters()

    # Assert
    assert len(shelters) > 0, "警告：沒讀到任何資料 請檢查 data_for_refuge 資料夾"

    first = shelters[0]
    assert first.name is not None
    assert isinstance(first.capacity, int)
    assert isinstance(first.lat, float)
    assert isinstance(first.lon, float)

# 以下是新增的
def test_fetcher_region_tag_and_coords():
    shelters = DataFetcher().get_shelters()
    regions = {s.name.split("]")[0].lstrip("[") for s in shelters}
    assert regions == {"HUALIEN", "TAITUNG", "YILAN"}
    for s in shelters:
        assert 21.5 <= s.lat <= 25.0, s.name
        assert 120.5 <= s.lon <= 122.5, s.name
        assert s.capacity >= 0, s.name
        # tools/build_shelters.py 會把地址補成「縣 + 鄉鎮…」
        assert s.address[:3] in ("宜蘭縣", "花蓮縣", "臺東縣"), s.name
        assert s.township, s.name
        assert "ˇ" not in s.address, s.name
    # 消防署點位檔：收容人數填 0 的只有少數幾筆，大多數都有數字
    assert sum(s.capacity == 0 for s in shelters) < 10
    assert len({s.name for s in shelters}) == len(shelters) > 600


def test_official_detail_fields_are_loaded():
    by_name = {s.name: s for s in DataFetcher().get_shelters()}
    s = by_name["[TAITUNG] 大武國小"]
    assert (s.township, s.village) == ("大武鄉", "大武村")
    assert s.hazards == ("水災", "震災", "土石流", "海嘯")
    assert (s.indoor, s.outdoor, s.vulnerable_ok) == (True, False, True)
    # 座標修正表：官方座標在新竹山區，改用 OpenStreetMap 上的位置
    assert abs(by_name["[YILAN] 澳花國小"].lat - 24.329) < 0.01
    # 各鄉都有的同名活動中心加上鄉鎮區分
    assert {"[YILAN] 南興社區活動中心（冬山鄉）", "[YILAN] 南興社區活動中心（蘇澳鎮）"} <= set(by_name)

def test_fetcher_missing_folder(tmp_path):
    fetcher = DataFetcher()
    fetcher.folder_path = str(tmp_path)
    assert fetcher.get_shelters() == []

def test_fetcher_skips_broken_file(tmp_path):
    (tmp_path / "test_shelter.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "good_shelter.json").write_text(
        '[{"name":"甲","total_vessel":"10","total_people":"2","lat":"23.5","lon":"121.5"}]',
        encoding="utf-8",
    )
    fetcher = DataFetcher()
    fetcher.folder_path = str(tmp_path)
    shelters = fetcher.get_shelters()
    assert len(shelters) == 1
    assert shelters[0].name == "[GOOD] 甲"
    assert shelters[0].capacity == 10
    assert shelters[0].current_people == 2


def test_fetcher_skips_rows_without_name_or_coords(tmp_path, caplog):
    import json
    rows = [
        {"name": "甲", "total_vessel": 10, "total_people": 0, "lat": 23.5, "lon": 121.5},
        {"total_vessel": 10, "lat": 23.5, "lon": 121.5},                       # 缺名稱
        {"name": "乙", "total_vessel": 10, "lat": None, "lon": 121.5},         # 缺座標
        {"name": "丙", "total_vessel": 10, "lat": 0, "lon": 0},                # (0, 0) 在海上
        {"name": "丁", "total_vessel": "many", "lat": 23.5, "lon": 121.5},     # 容量不是數字
        "not an object",
    ]
    (tmp_path / "test_shelter.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    fetcher = DataFetcher()
    fetcher.folder_path = str(tmp_path)
    shelters = fetcher.get_shelters()
    assert [s.name for s in shelters] == ["[TEST] 甲"]
    assert fetcher.skipped == 5
    assert "略過" in caplog.text


def test_fetcher_raises_when_every_file_is_broken(tmp_path):
    import pytest

    from services.data_fetcher import ShelterDataError
    (tmp_path / "a_shelter.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "b_shelter.json").write_text('{"name": "not a list"}', encoding="utf-8")
    fetcher = DataFetcher()
    fetcher.folder_path = str(tmp_path)
    with pytest.raises(ShelterDataError):
        fetcher.get_shelters()
