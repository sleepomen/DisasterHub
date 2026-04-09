import json
import os
import glob
from models.shelter import Shelter
from services.shelter_remote_fetch import (
    load_shelters_from_csv_url,
    merge_shelter_lists,
    remote_shelter_url_from_env,
)

class DataFetcher:
    def __init__(self):
        #指向data_for_refuge資料夾
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.folder_path = os.path.normpath(os.path.join(current_dir, "..", "data_for_refuge"))

    def get_shelters(self) -> list[Shelter]:
        all_shelters = []
        
        # 找json資料夾
        search_pattern = os.path.join(self.folder_path, "*.json")
        json_files = glob.glob(search_pattern)
        
        if not json_files:
            print(f"DEBUG: 掃描路徑為 {self.folder_path}")
            print(" 錯誤：找不到任何 .json 檔案")
            return []

        for file_path in json_files:
            filename = os.path.basename(file_path)
            # 自動取得地區名
            region_name = filename.split('_')[0].upper()
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        #把json轉為強型別的shelter物件
                        shelter = Shelter(
                            name=f"[{region_name}] {item.get('name')}",
                            total_vessel=int(item.get("total_vessel", 0)),
                            total_people=int(item.get("total_people", 0)),
                            lat=float(item.get("lat", 0.0)),
                            lon=float(item.get("lon", 0.0))
                        )
                        all_shelters.append(shelter)
                print(f"讀取 {filename}，total {len(data)} datas")
            except Exception as e:
                print(f"解析 {filename} error: {e}")

        # 在既有 mock JSON 之外，可經 OPENDATA_SHELTER_CSV_URL 併入政府開放 CSV（欄位別名見shelter_remote_fetch）；合併規則為同名保留先出現者；可搭配 OPENDATA_SHELTER_COUNTY_FILTER 限縣市。
        url = remote_shelter_url_from_env()
        if url:
            remote = load_shelters_from_csv_url(url)
            all_shelters = merge_shelter_lists(all_shelters, remote)

        return all_shelters