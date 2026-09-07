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

    def sync(self):
        """
        把 JSON 的靜態資料（容量 / 地址 / 座標）同步進資料庫。
        不會覆蓋既有列的 current_ppl，模擬回寫的佔用數要靠 /api/reset_simulation 才會清掉。
        """
        print("starting data synchronization...")
        shelters = self.fetcher.get_shelters()

        if not shelters:
            print("synchronization aborted, no data fetched.")
            return

        try:
            success_count = self.repository.upsert_shelters(shelters)
        except Exception as e:
            logger.exception("batch upsert failed, falling back to per-row upsert: %s", e)
            success_count = 0
            for s in shelters:
                try:
                    self.repository.upsert_shelter(s)
                    success_count += 1
                except Exception as row_err:
                    logger.error("error when upserting %s: %s", s.name, row_err)

        print(f"synchronization success {success_count}/{len(shelters)} times data")
