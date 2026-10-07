import logging

from repositories.shelter_repository import ShelterRepository
from services.data_fetcher import DataFetcher
from services.metrics import metrics

logger = logging.getLogger(__name__)

# 清理步驟的結果：刪了幾筆，以及因為來源不完整而「不敢刪」了幾次。
# 後者連續出現代表來源資料壞掉而沒人發現
metrics.register("sync.pruned", "sync.prune_skipped")

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

        寫完之後會清掉來源已經移除的避難所，但只在這次同步「完整可信」時才清——
        判斷條件見 _prune_removed()。
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

        pruned = self._prune_removed(shelters, success_count)
        logger.info(
            "同步完成：%d / %d 筆（來源略過 %d 筆，清掉已移除 %d 筆）",
            success_count, len(shelters), self.fetcher.skipped, pruned,
        )
        return success_count

    def _prune_removed(self, shelters, success_count: int) -> int:
        """
        刪掉來源已經沒有的避難所。這是整個同步流程裡唯一會刪資料的一步，所以三道前提
        都成立才做——任何一道不成立就寧可留著孤兒列，也不要刪掉其實還在的避難所：

        1. 每一筆來源資料都真的寫進去了。部分寫入失敗時，沒寫進去的那幾筆會被當成
           「來源已移除」而刪掉。
        2. 沒有任何來源檔案讀不起來。一個檔案就是一個縣，少一個檔案會讓整個縣被清空。
        3. 沒有任何單筆資料被略過。被略過的那幾筆在資料庫裡的列會被誤刪，
           連同模擬回寫的收容人數一起消失。
        """
        reason = None
        if success_count != len(shelters):
            reason = f"有 {len(shelters) - success_count} 筆沒寫進資料庫"
        elif self.fetcher.failed_files:
            reason = f"來源檔案 {'、'.join(self.fetcher.failed_files)} 讀不起來（整個縣會被誤刪）"
        elif self.fetcher.skipped:
            reason = f"來源略過了 {self.fetcher.skipped} 筆資料"
        if reason:
            metrics.incr("sync.prune_skipped")
            logger.warning("略過清理已移除的避難所：%s", reason)
            return 0

        pruned = self.repository.delete_missing([s.name for s in shelters])
        if pruned:
            metrics.incr("sync.pruned", pruned)
            logger.info("清掉 %d 筆已從來源移除的避難所", pruned)
        return pruned
