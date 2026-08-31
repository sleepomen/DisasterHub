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
        assert s.capacity > 0, s.name

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
