from models.shelter import NearbyShelter, Shelter
from repositories.db import Database

# 半徑與最近距離查詢都用 geom::geography（才能用公尺算真實距離），但 idx_shelters_geom 建在
# geometry 上，EXPLAIN 證實這兩個查詢一律 Seq Scan——關掉 seqscan 也一樣。運算式索引要跟查詢裡的
# 運算式完全相同才會被採用，所以這裡建的就是 (geom::geography)
GEOGRAPHY_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_shelters_geog ON shelters USING GIST ((geom::geography))"

SELECT_COLUMNS = (
    "name, capacity, current_ppl, ST_Y(geom::geometry), ST_X(geom::geometry), COALESCE(address, ''), "
    "COALESCE(township, ''), COALESCE(village, ''), COALESCE(hazards, '{}'), indoor, outdoor, vulnerable_ok, "
    "COALESCE(phone, ''), COALESCE(serves, '')"
)

# 消防署點位檔帶來的欄位。舊資料庫用 ADD COLUMN IF NOT EXISTS 補上，既有的列先是 NULL，下一次同步就會填好
SHELTER_DETAIL_COLUMNS_SQL = (
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS township VARCHAR(20) DEFAULT ''",
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS village VARCHAR(20) DEFAULT ''",
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS hazards TEXT[] DEFAULT '{}'",
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS indoor BOOLEAN",
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS outdoor BOOLEAN",
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS vulnerable_ok BOOLEAN",
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS phone VARCHAR(40) DEFAULT ''",
    "ALTER TABLE shelters ADD COLUMN IF NOT EXISTS serves TEXT DEFAULT ''",
)


def _row_to_shelter(row) -> Shelter:
    return Shelter(
        name=row[0],
        capacity=row[1],
        current_people=row[2],
        lat=row[3],
        lon=row[4],
        address=row[5],
        township=row[6],
        village=row[7],
        hazards=tuple(row[8] or ()),
        indoor=row[9],
        outdoor=row[10],
        vulnerable_ok=row[11],
        phone=row[12],
        serves=row[13],
    )


class ShelterQueries(Database):
    """避難所本身：同步寫入、收容人數、半徑與最近距離查詢"""

    # 同步只負責「靜態」欄位（容量 / 地址 / 座標 / 消防署點位檔的其他欄位）。
    # current_ppl 是模擬回寫的即時狀態，重啟或手動同步都不能把它洗掉；
    # 只有在容量縮小到低於目前人數時才往下壓，避免出現負的剩餘空間。
    UPSERT_SQL = """
        INSERT INTO shelters (name, capacity, current_ppl, address, geom,
                              township, village, hazards, indoor, outdoor, vulnerable_ok, phone, serves)
        VALUES (%s, %s, %s, %s, ST_SetSRID(ST_Point(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            capacity = EXCLUDED.capacity,
            current_ppl = LEAST(shelters.current_ppl, EXCLUDED.capacity),
            address = EXCLUDED.address,
            geom = EXCLUDED.geom,
            township = EXCLUDED.township,
            village = EXCLUDED.village,
            hazards = EXCLUDED.hazards,
            indoor = EXCLUDED.indoor,
            outdoor = EXCLUDED.outdoor,
            vulnerable_ok = EXCLUDED.vulnerable_ok,
            phone = EXCLUDED.phone,
            serves = EXCLUDED.serves;
    """

    # 一次更新多筆佔用數；人數夾在 [0, capacity] 之間，名稱不存在的列直接略過
    SET_OCCUPANCY_SQL = """
        UPDATE shelters AS s
        SET current_ppl = LEAST(GREATEST(v.ppl, 0), s.capacity)
        FROM (SELECT unnest(%s::text[]) AS name, unnest(%s::int[]) AS ppl) AS v
        WHERE s.name = v.name;
    """

    @staticmethod
    def _upsert_params(shelter: Shelter):
        return (
            shelter.name,
            shelter.capacity,
            shelter.current_people,
            shelter.address,
            shelter.lon,
            shelter.lat,
            shelter.township,
            shelter.village,
            list(shelter.hazards),
            shelter.indoor,
            shelter.outdoor,
            shelter.vulnerable_ok,
            shelter.phone,
            shelter.serves,
        )

    def upsert_shelter(self, shelter: Shelter):
        try:
            with self._cursor() as cursor:
                cursor.execute(self.UPSERT_SQL, self._upsert_params(shelter))
        except Exception as e:
            raise RuntimeError(f"upsert_shelter 失敗：{e}") from e

    def upsert_shelters(self, shelters: list[Shelter]) -> int:
        if not shelters:
            return 0
        params = [self._upsert_params(s) for s in shelters]
        try:
            with self._cursor() as cursor:
                cursor.executemany(self.UPSERT_SQL, params)
        except Exception as e:
            raise RuntimeError(f"upsert_shelters 失敗：{e}") from e
        return len(params)

    def set_occupancy(self, occupancy: dict[str, int]) -> int:
        """
        回寫各避難所目前收容人數（模擬結果 / 重置用）。回傳實際更新的列數。
        """
        if not occupancy:
            return 0
        names = list(occupancy.keys())
        counts = [int(occupancy[n]) for n in names]
        try:
            with self._cursor() as cursor:
                cursor.execute(self.SET_OCCUPANCY_SQL, (names, counts))
                return cursor.rowcount
        except Exception as e:
            raise RuntimeError(f"set_occupancy 失敗：{e}") from e

    # 清掉來源已經移除的避難所。沒有這一步，從 JSON 刪掉一間避難所之後，
    # 資料庫那一列會永遠留著，AI 會繼續推薦一個已經不是避難所的地點；
    # 改名更糟：舊名新名各一列。名稱為 NULL 的髒資料留著不動（NULL <> ALL 不成立）
    DELETE_MISSING_SQL = "DELETE FROM shelters WHERE name <> ALL(%s::text[]);"

    def delete_missing(self, keep_names: list[str]) -> int:
        """刪除名稱不在 keep_names 裡的避難所，回傳刪掉的列數"""
        if not keep_names:
            raise ValueError("delete_missing 需要一份非空的保留清單，空清單會清掉整張表")
        try:
            with self._cursor() as cursor:
                cursor.execute(self.DELETE_MISSING_SQL, (list(keep_names),))
                return cursor.rowcount
        except Exception as e:
            raise RuntimeError(f"delete_missing 失敗：{e}") from e

    def get_all_shelters(self):
        # 固定排序，讓向量索引指紋與前端列表在重啟後保持一致
        try:
            with self._cursor() as cursor:
                cursor.execute(f"SELECT {SELECT_COLUMNS} FROM shelters ORDER BY name")
                return [_row_to_shelter(row) for row in cursor.fetchall()]
        except Exception as e:
            raise RuntimeError(f"get_all_shelters error：{e}") from e

    def get_shelters_in_radius(self, lat: float, lon: float, radius_km: float) -> list[Shelter]:
        """
        使用 PostGIS 找出中心點半徑內的避難所。
        回傳 Shelter 物件（跟 get_all_shelters 一致），轉成 API 的 JSON 由 MapService 負責。
        依名稱排序：最小成本流在成本相同時照避難所的順序決定，沒有 ORDER BY 時順序跟著資料表的實體順序走，
        模擬回寫收容人數（UPDATE）之後就會變，同一個情境再跑一次分配就不一樣（容量報告的開車人數會差幾個人）
        """
        impacted: list[Shelter] = []
        try:
            with self._cursor() as cursor:
                sql = f"""
                    SELECT {SELECT_COLUMNS}
                    FROM shelters
                    WHERE ST_DWithin(
                        geom,
                        ST_SetSRID(ST_Point(%s, %s), 4326)::geography,
                        %s
                    )
                    ORDER BY name;
                """
                cursor.execute(sql, (lon, lat, radius_km * 1000))
                impacted = [_row_to_shelter(row) for row in cursor.fetchall()]
        except Exception as e:
            raise RuntimeError(f"get_shelters_in_radius 失敗：{e}") from e
        return impacted

    def get_nearest_shelters(self, lat: float, lon: float, limit: int = 5) -> list[NearbyShelter]:
        """
        使用 PostGIS ST_Distance 找出距離中心點最近的 N 個避難所，依距離排序。
        距離是這次查詢才有的值，所以包成 NearbyShelter 而不是塞進 Shelter。
        """
        nearest: list[NearbyShelter] = []
        try:
            with self._cursor() as cursor:
                sql = f"""
                    SELECT
                        {SELECT_COLUMNS},
                        ROUND(
                            ST_Distance(
                                geom::geography,
                                ST_SetSRID(ST_Point(%s, %s), 4326)::geography
                            )::numeric / 1000, 2
                        ) AS distance_km
                    FROM shelters
                    ORDER BY geom::geography <-> ST_SetSRID(ST_Point(%s, %s), 4326)::geography
                    LIMIT %s;
                """
                cursor.execute(sql, (lon, lat, lon, lat, limit))
                nearest = [
                    # 距離是 SELECT_COLUMNS 之後的最後一欄；寫死位置的話，SELECT_COLUMNS 一加欄位就會讀錯
                    NearbyShelter(shelter=_row_to_shelter(row), distance_km=float(row[-1]))
                    for row in cursor.fetchall()
                ]
        except Exception as e:
            raise RuntimeError(f"get_nearest_shelters 失敗：{e}") from e
        return nearest
