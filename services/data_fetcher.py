import glob
import json
import logging
import os

from models.shelter import Shelter

logger = logging.getLogger(__name__)


class ShelterDataError(RuntimeError):
    """資料夾裡有 JSON 檔，但一筆避難所都讀不出來"""


def _parse_item(item: dict, region_name: str) -> Shelter:
    """
    把一筆 JSON 轉成 Shelter。缺名稱、座標不合法、容量不是數字都直接丟 ValueError，
    由呼叫端決定跳過；不能默默變成「[YILAN] None」或落在 (0, 0) 海上的避難所。
    """
    if not isinstance(item, dict):
        raise ValueError("不是物件")
    name = str(item.get("name") or "").strip()
    if not name:
        raise ValueError("缺少 name")
    try:
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
    except (TypeError, ValueError):
        raise ValueError("lat / lon 缺少或不是數字") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        raise ValueError(f"座標不合法（{lat}, {lon}）")
    try:
        capacity = int(item.get("total_vessel", 0))
        current = int(item.get("total_people", 0))
    except (TypeError, ValueError):
        raise ValueError("total_vessel / total_people 不是整數") from None
    if capacity < 0 or current < 0:
        raise ValueError("容量或人數不能是負數")
    return Shelter(
        name=f"[{region_name}] {name}",
        capacity=capacity,
        current_people=current,
        lat=lat,
        lon=lon,
        address=str(item.get("address") or "").strip(),
    )


class DataFetcher:
    def __init__(self):
        #指向data_for_refuge資料夾
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.folder_path = os.path.normpath(os.path.join(current_dir, "..", "data_for_refuge"))
        self.skipped = 0
        # 整個檔案讀不起來的清單。這跟 skipped（單筆壞掉）要分開記：
        # 一個檔案就是一個縣，少一個檔案代表那一縣在這次同步裡「看起來不存在」，
        # 同步端必須靠這個訊號決定不要清理，否則會把整個縣當成已移除而刪掉
        self.failed_files: list[str] = []

    def get_shelters(self) -> list[Shelter]:
        """
        讀取資料夾裡所有 JSON。壞掉的檔案或壞掉的單筆資料會跳過並記 log，
        跳過的筆數累計在 self.skipped；有檔案卻一筆都讀不出來時丟 ShelterDataError，
        呼叫端才不會把「資料全壞」當成同步成功。
        """
        all_shelters = []
        self.skipped = 0
        self.failed_files = []

        # 找json資料夾
        search_pattern = os.path.join(self.folder_path, "*.json")
        json_files = glob.glob(search_pattern)

        if not json_files:
            logger.error("找不到任何 .json 檔案，掃描路徑為 %s", self.folder_path)
            return []

        for file_path in sorted(json_files):
            filename = os.path.basename(file_path)
            # 自動取得地區名
            region_name = filename.split('_')[0].upper()

            try:
                with open(file_path, encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise ValueError("最外層必須是陣列")
            except Exception as e:
                logger.error("解析 %s 失敗: %s", filename, e)
                self.failed_files.append(filename)
                continue

            loaded = 0
            for index, item in enumerate(data):
                try:
                    all_shelters.append(_parse_item(item, region_name))
                    loaded += 1
                except ValueError as e:
                    self.skipped += 1
                    logger.warning("略過 %s 第 %d 筆：%s", filename, index + 1, e)
            logger.info("讀取 %s，共 %d 筆（略過 %d 筆）", filename, loaded, len(data) - loaded)

        if not all_shelters:
            raise ShelterDataError(
                f"{self.folder_path} 裡有 {len(json_files)} 個 JSON 檔，但沒有任何一筆合法的避難所資料"
            )
        return all_shelters
