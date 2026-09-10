import logging
from services.data_fetcher import DataFetcher
from repositories.shelter_repository import ShelterRepository

logger = logging.getLogger(__name__)

class DataSyncService:
    #初始化fetcher/repository
    def __init__(self):
        self.fetcher = DataFetcher()
        self.repository = ShelterRepository()

    def baseline_occupancy(self) -> dict[str, int]:
        """
        來源 JSON 裡的初始收容人數，重置模擬時用來把 current_ppl 還原
        """
        return {s.name: s.current_people for s in self.fetcher.get_shelters()}

    def sync(self) -> int:
        """
        把 JSON 的靜態資料（容量 / 地址 / 座標）同步進資料庫，回傳成功寫入的筆數。
        不會覆蓋既有列的 current_ppl，模擬回寫的佔用數要靠 /api/reset_simulation 才會清掉。
        讀不到任何資料時丟 RuntimeError，啟動流程與 /api/sync 才不會把空同步當成成功。
        """
        logger.info("開始同步避難所資料")
        shelters = self.fetcher.get_shelters()

        if not shelters:
            raise RuntimeError("同步中止：來源資料夾沒有任何避難所資料")

        try:
            success_count = self.repository.upsert_shelters(shelters)
        except Exception as e:
            logger.exception("批次寫入失敗，改為逐筆寫入：%s", e)
            success_count = 0
            for s in shelters:
                try:
                    self.repository.upsert_shelter(s)
                    success_count += 1
                except Exception as row_err:
                    logger.error("寫入 %s 失敗：%s", s.name, row_err)

        if success_count == 0:
            raise RuntimeError(f"同步失敗：{len(shelters)} 筆資料沒有任何一筆寫入資料庫")
        logger.info("同步完成：%d / %d 筆（來源略過 %d 筆）", success_count, len(shelters), self.fetcher.skipped)
        return success_count
