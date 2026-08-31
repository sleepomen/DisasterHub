import json
import os
import glob
import logging
from models.shelter import Shelter

logger = logging.getLogger(__name__)

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
            logger.error("找不到任何 .json 檔案，掃描路徑為 %s", self.folder_path)
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
                            capacity=int(item.get("total_vessel", 0)),
                            current_people=int(item.get("total_people", 0)),
                            lat=float(item.get("lat", 0.0)),
                            lon=float(item.get("lon", 0.0))
                        )
                        all_shelters.append(shelter)
                logger.info("讀取 %s，共 %d 筆", filename, len(data))
            except Exception as e:
                logger.error("解析 %s 失敗: %s", filename, e)

        return all_shelters
