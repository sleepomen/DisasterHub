from typing import NamedTuple

from models.travel import DEFAULT_DRIVE_KMH, DRIVE_KMH, TRAVEL_MODES, WALK_MPS
from repositories.db import Database

# 路網（pgRouting 的邊表）。cost / reverse_cost 是公尺，兩個方向一樣長（步行不管單行道）；
# oneway = 1 代表開車只能 source → target，開車時查詢才把反方向擋掉（見 _routing_sql）。
# KNN 吸附用 geometry 的 GiST 索引
ROAD_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS roads (
        id INTEGER PRIMARY KEY,
        source INTEGER NOT NULL,
        target INTEGER NOT NULL,
        cost DOUBLE PRECISION NOT NULL,
        reverse_cost DOUBLE PRECISION NOT NULL,
        highway VARCHAR(20),
        geom GEOMETRY(LineString, 4326)
    )
    """,
    "ALTER TABLE roads ADD COLUMN IF NOT EXISTS oneway SMALLINT NOT NULL DEFAULT 0",
    # 路名、路線編號（台9、縣193）、是不是橋、車道數（0 = OSM 沒標）：瓶頸清單與道路容量用
    "ALTER TABLE roads ADD COLUMN IF NOT EXISTS name VARCHAR(60) NOT NULL DEFAULT ''",
    "ALTER TABLE roads ADD COLUMN IF NOT EXISTS ref VARCHAR(30) NOT NULL DEFAULT ''",
    "ALTER TABLE roads ADD COLUMN IF NOT EXISTS bridge BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE roads ADD COLUMN IF NOT EXISTS lanes SMALLINT NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_roads_geom ON roads USING GIST (geom)",
    # 大檔案每次啟動都重灌太慢：記下匯入的是哪一版，內容沒變就跳過
    """
    CREATE TABLE IF NOT EXISTS dataset_versions (
        name VARCHAR(40) PRIMARY KEY,
        checksum VARCHAR(64) NOT NULL,
        loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)

# 點離最近的道路超過這個距離就不吸附，該點不算道路距離（例如海上、深山）
ROAD_SNAP_M = 1000
# 疏散起點以前是鄉鎮人口重心，只是一個代表點，可能落在田裡或河床上，所以起點可以吸附得比較遠，
# 那一段走到路上的距離照樣算進道路距離。現在起點是村里裡離道路 1 公里內、建物最密的那棟
# （tools/build_villages.py），554 個離路的中位數 19 公尺、最遠 336 公尺（達仁鄉台坂村），這個寬限只是保險
ORIGIN_SNAP_M = 5000
# 路徑可能繞出起訖點的外接框（沿海岸繞過山）。框往外擴大這麼多度，再至少擴大框本身的一半
ROUTE_MARGIN_DEG = 0.05
FRACTION_EPS = 1e-6
# 路線幾何回傳前的簡化容許誤差（度，約 10 公尺）：地圖上看不出差別，疏散模擬一次幾十條路線的資料量少很多
PATH_SIMPLIFY_DEG = 0.0001

# 路線避開災害範圍的加權（成本 = 長度 × 係數）。係數的意思是「願意多繞幾倍路來避開」：
# 高風險潛勢範圍內的 1 公里，寧可繞 3 公里的安全道路。只拿來挑路線，回報的距離仍是實際公尺數
ROUTE_HAZARD_FACTORS = {"debris_flow": {"高": 3.0, "中": 2.0, "低": 1.5, "持續觀察": 1.5}}
DEFAULT_RISK_FACTOR = 1.5
# 黃色警戒（預防性疏散）範圍內的道路：還能走，但盡量繞開。紅色警戒範圍內的道路直接封閉
CAUTION_FACTOR = 5.0


class RoadPath(NamedTuple):
    """
    一條實際路線：距離（公尺）、其中仍經過災害範圍的長度、路上那段的 encoded polyline、
    開車沿這條路要幾秒（走到路上、離開道路的兩小段用步行速度）。步行時間就是 meters / WALK_MPS。
    edges 是路上那段依序走過的邊：(邊編號, 是否順著 source → target, 走了幾公尺)，算道路瓶頸用
    """
    meters: float
    hazard_m: float
    polyline: str
    drive_s: float = 0.0
    edges: tuple = ()

    @property
    def walk_s(self) -> float:
        return self.meters / WALK_MPS


# 路網檔每條邊 polyline 後面的欄位與預設值：單行道、路名、路線編號、橋、車道數。舊版的檔案後面幾欄可能沒有
EDGE_EXTRA_DEFAULTS = (0, "", "", 0, 0)


def edge_extras(rest: list) -> tuple:
    oneway, name, ref, bridge, lanes = (*rest, *EDGE_EXTRA_DEFAULTS[len(rest):])[:len(EDGE_EXTRA_DEFAULTS)]
    return oneway, name, ref, bool(bridge), lanes



class RoadQueries(Database):
    """路網：匯入、吸附、路線成本與實際路徑（pgRouting），路線避開災害範圍的加權"""

    def replace_road_network(self, edges: list[list], checksum: str) -> int | None:
        """
        整批換掉路網，回傳寫入的邊數；內容跟上次匯入的一樣（checksum 相同）就跳過、回 None。
        邊的格式見 tools/build_road_network.py：[source, target, 長度公尺, 道路等級, encoded polyline,
        單行道, 路名, 路線編號, 橋, 車道數]。舊版的路網檔後面幾欄可能沒有，缺的用預設值（雙向、沒有名字、不是橋、車道數不明）。
        在同一個交易裡清空再寫入，寫到一半失敗會整批回滾
        """
        from psycopg2.extras import execute_values

        try:
            with self._cursor() as cursor:
                cursor.execute("SELECT checksum FROM dataset_versions WHERE name = 'roads'")
                row = cursor.fetchone()
                if row and row[0] == checksum:
                    return None
                cursor.execute("TRUNCATE roads")
                execute_values(
                    cursor,
                    "INSERT INTO roads (id, source, target, cost, reverse_cost, highway, oneway, name, ref, bridge, lanes, geom) "
                    "VALUES %s",
                    [(i, s, t, length, length, highway, *edge_extras(rest), polyline)
                     for i, (s, t, length, highway, polyline, *rest) in enumerate(edges, 1)],
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, ST_LineFromEncodedPolyline(%s, 5))",
                    page_size=2000,
                )
                cursor.execute(
                    """
                    INSERT INTO dataset_versions (name, checksum) VALUES ('roads', %s)
                    ON CONFLICT (name) DO UPDATE SET checksum = EXCLUDED.checksum, loaded_at = now()
                    """,
                    (checksum,),
                )
                cursor.execute("ANALYZE roads")
            return len(edges)
        except Exception as e:
            raise RuntimeError(f"replace_road_network 失敗：{e}") from e

    # 每個點吸附到最近的一條道路：邊的編號、在邊上的位置（0～1）、點到路的距離（公尺）。
    # KNN 用 geometry 的 <->（單位是度，東部緯度下經緯度的比例差不到一成，挑最近的一條夠用）。
    # 長度為 0 的邊（簡化後兩端落在同一公尺格內）算不出位置，不拿來吸附
    SNAP_SQL = """
        SELECT t.idx, r.id, ST_LineLocatePoint(r.geom, t.g), ST_Distance(r.geom::geography, t.g::geography)
        FROM (
            SELECT idx, ST_SetSRID(ST_Point(lon, lat), 4326) AS g
            FROM unnest(%s::int[], %s::float8[], %s::float8[]) AS u(idx, lon, lat)
        ) t
        CROSS JOIN LATERAL (
            SELECT id, geom FROM roads WHERE ST_Length(geom) > 0 ORDER BY geom <-> t.g LIMIT 1
        ) r
    """

    def road_distances(
        self, origins: list[tuple[float, float]], targets: list[tuple[float, float]],
        snap_m: float = ROAD_SNAP_M, origin_snap_m: float | None = None, hazard: str | None = None,
        mode: str = "walk",
    ) -> dict[tuple[int, int], float | None]:
        """
        起點 × 終點的路線成本（秒），key 是 (起點序號, 終點序號)，座標是 (lat, lon)。
        成本＝起點走到最近道路＋沿路網的最短路徑＋終點離開道路的那一段，依 mode 換算成時間
        （步行或開車，見 models/travel.py；頭尾兩小段一律步行）。路上那段每條邊再依災害範圍加權
        （見 _weights_sql）：沒有警戒、也沒指定 hazard 時，成本就是實際的通行時間。
        排序用這個成本；實際距離與時間由 road_paths 算。

        值是 None 代表到不了：兩端都吸附到道路，整個路網卻找不到不經過封閉道路的路徑。
        吸附不到道路（終點離路超過 snap_m、起點超過 origin_snap_m，沒給就跟 snap_m 一樣）的組合
        不會出現在結果裡，由呼叫端退回直線距離
        """
        if not origins or not targets:
            return {}
        points = list(origins) + list(targets)
        try:
            with self._cursor() as cursor:
                snaps, starts, ends = self._snap(cursor, origins, targets, snap_m, origin_snap_m)
                if not starts or not ends:
                    return {}
                routed = self._route_costs(cursor, points, snaps, starts, ends, hazard, mode, bounded=True)
                missing = [
                    (i, k) for i in starts for k in ends
                    if (i, k) not in routed and snaps[i][:2] != snaps[k][:2]
                ]
                if missing:
                    # 繞開封閉道路的路線可能走出起訖點周圍的子圖：只對算不出來的組合，用整個路網再算一次
                    retry_starts = sorted({i for i, _ in missing})
                    retry_ends = sorted({k for _, k in missing})
                    routed |= self._route_costs(
                        cursor, points, snaps, retry_starts, retry_ends, hazard, mode, bounded=False,
                    )
        except Exception as e:
            raise RuntimeError(f"road_distances 失敗：{e}") from e

        result = {}
        for i in starts:
            for k in ends:
                cost = routed.get((i, k))
                # 起訖吸附到同一個位置時 pgRouting 不回傳這一組，路上距離就是 0
                if cost is None and snaps[i][:2] == snaps[k][:2]:
                    cost = 0.0
                off_road = (snaps[i][2] + snaps[k][2]) / WALK_MPS
                result[(i, k - len(origins))] = None if cost is None else cost + off_road
        return result

    def road_paths(
        self, origins: list[tuple[float, float]], targets: list[tuple[float, float]], pairs: list[tuple[int, int]],
        snap_m: float = ROAD_SNAP_M, origin_snap_m: float | None = None, hazard: str | None = None,
        mode: str = "walk",
    ) -> dict[tuple[int, int], RoadPath]:
        """
        指定的 (起點序號, 終點序號) 組合沿路網的實際路線。回傳 RoadPath：
          meters    實際距離（公尺），含走到路上、離開道路的兩小段，沒有加權
          hazard_m  路上那段裡仍經過災害範圍（潛勢範圍或警戒範圍）的長度
          polyline  路上那段的 encoded polyline（precision 5，lat/lon），頭尾兩小段由畫圖的一方補直線
          drive_s   開車沿這條路的秒數（頭尾兩小段步行）
        路線依 mode 選：步行選最短的，開車選最快的
        吸附、子圖範圍、加權都跟 road_distances 一樣，畫出來的就是排序用的那一條。到不了的組合不在結果裡
        """
        if not pairs:
            return {}
        points = list(origins) + list(targets)
        n = len(origins)
        try:
            with self._cursor() as cursor:
                snaps, starts, ends = self._snap(cursor, origins, targets, snap_m, origin_snap_m)
                wanted = [(i, n + j) for i, j in pairs if i in starts and n + j in ends]
                # 起訖吸附到道路上同一個位置時 pgRouting 不回傳路線：路上那段是 0，只剩走到路上、離開道路的兩小段。
                # 小地名用村里避難所的位置當座標時，最近的那間就是起點本身（2026-11-02 實測「新社」）
                same_spot = {pair: (0.0, 0.0, "", 0.0, ()) for pair in wanted if snaps[pair[0]][:2] == snaps[pair[1]][:2]}
                wanted = [pair for pair in wanted if pair not in same_spot]
                found = dict(same_spot)
                if wanted:
                    found |= self._route_paths(cursor, points, snaps, wanted, hazard, mode, bounded=True)
                    missing = [pair for pair in wanted if pair not in found]
                    if missing:
                        found |= self._route_paths(cursor, points, snaps, missing, hazard, mode, bounded=False)
        except Exception as e:
            raise RuntimeError(f"road_paths 失敗：{e}") from e
        result = {}
        for (i, k), (meters, hazard_m, polyline, drive_s, edges) in found.items():
            off_road = snaps[i][2] + snaps[k][2]
            result[(i, k - n)] = RoadPath(meters + off_road, hazard_m, polyline, drive_s + off_road / WALK_MPS, edges)
        return result

    def road_edges(self, edge_ids: list[int]) -> dict[int, dict]:
        """
        道路瓶頸要的邊屬性：{邊編號: {highway, lanes, oneway, name, ref, bridge, length_m, lat, lon}}。
        lat / lon 是這條邊的中點，地圖上標瓶頸的位置
        """
        if not edge_ids:
            return {}
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, highway, lanes, oneway, name, ref, bridge, cost,
                           ST_Y(ST_LineInterpolatePoint(geom, 0.5)), ST_X(ST_LineInterpolatePoint(geom, 0.5))
                    FROM roads WHERE id = ANY(%s)
                    """,
                    (sorted(set(edge_ids)),),
                )
                return {
                    row[0]: {
                        "highway": row[1], "lanes": row[2], "oneway": row[3], "name": row[4], "ref": row[5],
                        "bridge": row[6], "length_m": float(row[7]), "lat": float(row[8]), "lon": float(row[9]),
                    }
                    for row in cursor.fetchall()
                }
        except Exception as e:
            raise RuntimeError(f"road_edges 失敗：{e}") from e

    def _route_costs(self, cursor, points, snaps, starts, ends, hazard, mode, bounded: bool) -> dict:
        edges_sql, points_sql = self._routing_sql(points, snaps, starts + ends, starts, hazard, mode, bounded)
        cursor.execute(
            """
            SELECT start_pid, end_pid, agg_cost
            FROM pgr_withPointsCost(%s, %s, %s::bigint[], %s::bigint[], directed := %s)
            """,
            (edges_sql, points_sql, [-(i + 1) for i in starts], [-(i + 1) for i in ends], mode == "drive"),
        )
        return {(abs(s) - 1, abs(e) - 1): float(cost) for s, e, cost in cursor.fetchall()}

    def _route_paths(self, cursor, points, snaps, wanted, hazard, mode, bounded: bool) -> dict:
        used = sorted({p for pair in wanted for p in pair})
        starts = sorted({i for i, _ in wanted})
        edges_sql, points_sql = self._routing_sql(points, snaps, used, starts, hazard, mode, bounded)
        values = ", ".join(f"({-(i + 1)}, {-(k + 1)})" for i, k in wanted)
        combinations_sql = f"SELECT * FROM (VALUES {values}) AS c(source, target)"
        cursor.execute(self.PATH_SQL.format(weights=self._weights_sql(hazard), drive_mps=self._drive_mps_sql()), (
            [i + 1 for i in used], [snaps[i][1] for i in used],
            edges_sql, points_sql, combinations_sql, mode == "drive", PATH_SIMPLIFY_DEG,
        ))
        return {
            (abs(s) - 1, abs(e) - 1): (
                float(meters), float(hazard_m), polyline, float(drive_s),
                tuple(zip(edge_ids or (), forward or (), [float(m) for m in edge_m or ()], strict=True)),
            )
            for s, e, meters, hazard_m, polyline, drive_s, edge_ids, forward, edge_m in cursor.fetchall()
        }

    # 把 pgr_withPoints 走過的邊接成一條線。吸附點在路徑上是負的節點編號，所在的那條邊只走一部分：
    # 每一步依「從哪個節點、到哪個節點」換算成邊上的位置（端點 0 或 1、吸附點用它的 fraction），
    # 用 ST_LineSubstring 切出那一段；逆著邊的方向走就反轉，整條線才是從起點連到終點。
    # 實際距離用 roads.cost（原始長度）乘上走過的比例；受限的邊再乘上落在範圍內的比例，加總成 hazard_m；
    # 開車秒數依道路等級的速度另外加總（步行秒數就是距離除以步行速度，不必在 SQL 算）。
    # 最後三欄是依序走過的邊、是否順著 source → target（f0 < f1）、每條邊走了幾公尺，算道路瓶頸用
    PATH_SQL = """
        WITH pts AS (
            SELECT * FROM unnest(%s::bigint[], %s::float8[]) AS p(pid, fraction)
        ), path AS (
            SELECT start_pid, end_pid, path_seq, node, edge,
                   LEAD(node) OVER (PARTITION BY start_pid, end_pid ORDER BY path_seq) AS next_node
            FROM pgr_withPoints(%s, %s, %s, directed := %s)
        ), w AS ({weights}), seg AS (
            SELECT path.start_pid, path.end_pid, path.path_seq, path.edge, r.geom, r.cost AS length,
                   {drive_mps} AS drive_mps,
                   COALESCE(w.ratio, 0) AS hazard_ratio,
                   CASE WHEN path.node < 0 THEN (SELECT fraction FROM pts WHERE pid = -path.node)
                        WHEN path.node = r.source THEN 0 ELSE 1 END AS f0,
                   CASE WHEN path.next_node < 0 THEN (SELECT fraction FROM pts WHERE pid = -path.next_node)
                        WHEN path.next_node = r.source THEN 0 ELSE 1 END AS f1
            FROM path
            JOIN roads r ON r.id = path.edge
            LEFT JOIN w ON w.edge_id = path.edge
        )
        SELECT start_pid, end_pid,
               SUM(length * abs(f1 - f0)),
               SUM(length * abs(f1 - f0) * hazard_ratio),
               ST_AsEncodedPolyline(ST_Simplify(ST_MakeLine(
                   CASE WHEN f0 < f1 THEN ST_LineSubstring(geom, f0, f1)
                        ELSE ST_Reverse(ST_LineSubstring(geom, f1, f0)) END
                   ORDER BY path_seq
               ), %s), 5),
               SUM(length * abs(f1 - f0) / drive_mps),
               array_agg(edge ORDER BY path_seq),
               array_agg(f0 < f1 ORDER BY path_seq),
               array_agg(length * abs(f1 - f0) ORDER BY path_seq)
        FROM seg WHERE f0 <> f1
        GROUP BY start_pid, end_pid
    """

    def _snap(self, cursor, origins, targets, snap_m: float, origin_snap_m: float | None):
        """
        每個點吸附到最近的道路。回傳 snaps {點序號: (邊, fraction, 離路公尺)}、吸附得到的起點與終點序號。
        終點的序號接在起點後面（len(origins) + j）
        """
        origin_snap_m = snap_m if origin_snap_m is None else origin_snap_m
        points = list(origins) + list(targets)
        cursor.execute(self.SNAP_SQL, (
            list(range(len(points))), [lon for _, lon in points], [lat for lat, _ in points],
        ))
        # 吸附點常常剛好落在路段端點（fraction 0 或 1），pgRouting 3.4 的 withPoints 遇到這種點
        # 會整組回傳空結果（實測花蓮火車站一筆都算不出來）。往內夾一點點，誤差不到一公釐
        snaps = {
            idx: (edge_id, min(max(float(fraction), FRACTION_EPS), 1 - FRACTION_EPS), float(offset))
            for idx, edge_id, fraction, offset in cursor.fetchall()
            if offset <= (origin_snap_m if idx < len(origins) else snap_m)
        }
        starts = [i for i in range(len(origins)) if i in snaps]
        ends = [len(origins) + j for j in range(len(targets)) if len(origins) + j in snaps]
        return snaps, starts, ends

    @staticmethod
    def _weights_sql(hazard: str | None) -> str:
        """
        每條受限道路邊的 (edge_id, closed, factor)：
        - road_closures：紅色警戒範圍內封閉，黃色警戒範圍內成本 × CAUTION_FACTOR
        - hazard 有指定時（淹水模擬 → 土石流潛勢範圍），範圍內的道路依風險等級加權，不封閉：
          潛勢範圍涵蓋 564 公里道路，包含蘇花、花東沿線的省道，全部封掉東部會被切斷
        一條邊落在好幾個範圍裡取最嚴格的
        """
        parts = [
            "SELECT edge_id, level = 'closed' AS closed, "
            f"CASE level WHEN 'caution' THEN {CAUTION_FACTOR} ELSE 1 END AS factor, inside_ratio FROM road_closures"
        ]
        if hazard is not None:
            factors = ROUTE_HAZARD_FACTORS[hazard]  # 不認得的災害類型直接 KeyError，不會把字串拼進 SQL
            cases = " ".join(f"WHEN '{risk}' THEN {f}" for risk, f in factors.items())
            parts.append(
                f"SELECT edge_id, false, CASE risk {cases} ELSE {DEFAULT_RISK_FACTOR} END, inside_ratio "
                f"FROM road_hazards WHERE hazard_type = '{hazard}'"
            )
        union = " UNION ALL ".join(parts)
        # 加權只套在落在範圍內的那一段：成本 = 長度 ×（1 +（係數 − 1）× 範圍內比例）
        return (
            "SELECT edge_id, bool_or(closed) AS closed, max(1 + (factor - 1) * inside_ratio) AS factor, "
            f"max(inside_ratio) AS ratio FROM ({union}) x GROUP BY edge_id"
        )

    @staticmethod
    def _drive_mps_sql() -> str:
        """這條邊開車的速度（公尺／秒），依 OSM 道路等級"""
        cases = " ".join(f"WHEN '{highway}' THEN {kmh}" for highway, kmh in DRIVE_KMH.items())
        return f"(CASE r.highway {cases} ELSE {DEFAULT_DRIVE_KMH} END / 3.6)"

    @classmethod
    def _routing_sql(
        cls, points: list[tuple[float, float]], snaps: dict, used: list[int], starts: list[int],
        hazard: str | None, mode: str = "walk", bounded: bool = True,
    ) -> tuple[str, str]:
        """
        pgRouting 的邊表與吸附點 SQL。成本是秒：長度除以這種交通方式在這條路上的速度，再依災害範圍加權。
        bounded 時邊只取這些點外接框（往外擴）內的，不必每次搜整個東部。
        封閉的邊成本給 -1，pgRouting 會當成這條邊不存在。起點所在的那條邊例外：
        人就在警戒範圍裡，正是要往外撤的人，不能因為腳下的路封了就困在原地
        """
        # pgRouting 收的是 SQL 字串；放進去的只有這裡算出來的數字與固定的設定，不是使用者的文字
        if mode not in TRAVEL_MODES:
            raise ValueError(f"不支援的交通方式：{mode!r}")
        speed = str(WALK_MPS) if mode == "walk" else cls._drive_mps_sql()
        open_edges = ", ".join(str(snaps[i][0]) for i in starts) or "0"
        where = ""
        if bounded:
            lats = [points[i][0] for i in used]
            lons = [points[i][1] for i in used]
            pad_lat = ROUTE_MARGIN_DEG + (max(lats) - min(lats)) / 2
            pad_lon = ROUTE_MARGIN_DEG + (max(lons) - min(lons)) / 2
            where = (
                f"WHERE r.geom && ST_MakeEnvelope({min(lons) - pad_lon}, {min(lats) - pad_lat}, "
                f"{max(lons) + pad_lon}, {max(lats) + pad_lat}, 4326)"
            )
        closed = f"w.closed AND r.id NOT IN ({open_edges})"
        # 開車時單行道的反方向不能走（成本 -1），圖也改成有向；步行兩個方向都能走
        against_oneway = " OR r.oneway = 1" if mode == "drive" else ""
        edges_sql = (
            "SELECT r.id, r.source, r.target, "
            f"CASE WHEN {closed} THEN -1 ELSE r.cost / {speed} * COALESCE(w.factor, 1) END AS cost, "
            f"CASE WHEN {closed}{against_oneway} THEN -1 ELSE r.reverse_cost / {speed} * COALESCE(w.factor, 1) END AS reverse_cost "
            f"FROM roads r LEFT JOIN ({cls._weights_sql(hazard)}) w ON w.edge_id = r.id {where}"
        )
        values = ", ".join(f"({i + 1}, {snaps[i][0]}, {snaps[i][1]})" for i in used)
        points_sql = f"SELECT pid, edge_id, fraction FROM (VALUES {values}) AS p(pid, edge_id, fraction)"
        return edges_sql, points_sql
