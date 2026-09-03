import logging
from services.data_fetcher import DataFetcher
from repositories.shelter_repository import ShelterRepository

logger = logging.getLogger(__name__)

class DataSyncService:
    #初始化fetcher/repository
    def __init__(self):
        self.fetcher = DataFetcher()
        self.repository = ShelterRepository()

    def sync(self):
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
