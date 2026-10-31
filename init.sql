CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS shelters (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) UNIQUE,
    geom GEOMETRY(Point, 4326),
    capacity INTEGER,
    current_ppl INTEGER DEFAULT 0,
    address VARCHAR(200) DEFAULT '',
    -- 以下來自消防署避難收容處所點位檔
    township VARCHAR(20) DEFAULT '',
    village VARCHAR(20) DEFAULT '',
    hazards TEXT[] DEFAULT '{}',
    indoor BOOLEAN,
    outdoor BOOLEAN,
    vulnerable_ok BOOLEAN,
    phone VARCHAR(40) DEFAULT '',
    serves TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_shelters_geom ON shelters USING GIST (geom);
-- 距離查詢都轉成 geography 算公尺，只有建在同一個運算式上的索引才會被採用
CREATE INDEX IF NOT EXISTS idx_shelters_geog ON shelters USING GIST ((geom::geography));

-- 災害潛勢區域（土石流潛勢溪流影響範圍等），由啟動同步從 data_reference/ 的 GeoJSON 匯入
CREATE TABLE IF NOT EXISTS hazard_zones (
    id SERIAL PRIMARY KEY,
    hazard_type VARCHAR(40) NOT NULL,
    ref_no VARCHAR(40),
    county VARCHAR(20),
    town VARCHAR(20),
    village VARCHAR(20),
    risk VARCHAR(10),
    households INTEGER,
    geom GEOMETRY(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_hazard_zones_geog ON hazard_zones USING GIST ((geom::geography));
CREATE INDEX IF NOT EXISTS idx_hazard_zones_type ON hazard_zones (hazard_type);

-- 路網（pgRouting 的邊表，cost / reverse_cost 單位是公尺），由啟動同步從 data_reference/east_roads.json.gz 匯入。
-- 映像沒有 pgRouting 時不讓初始化整個失敗，距離查詢會退回直線距離
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pgrouting;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgRouting 無法啟用：%', SQLERRM;
END $$;

CREATE TABLE IF NOT EXISTS roads (
    id INTEGER PRIMARY KEY,
    source INTEGER NOT NULL,
    target INTEGER NOT NULL,
    cost DOUBLE PRECISION NOT NULL,
    reverse_cost DOUBLE PRECISION NOT NULL,
    highway VARCHAR(20),
    geom GEOMETRY(LineString, 4326)
);
CREATE INDEX IF NOT EXISTS idx_roads_geom ON roads USING GIST (geom);

-- 大型資料集匯入的是哪一版，內容沒變就不重灌
CREATE TABLE IF NOT EXISTS dataset_versions (
    name VARCHAR(40) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 路線避開災害範圍：道路邊與潛勢範圍的對應、即時警戒、目前生效的道路限制（視圖）。
-- 說明見 repositories/shelter_repository.py 的 ROUTE_HAZARD_SCHEMA_SQL；應用程式啟動時也會建立
CREATE TABLE IF NOT EXISTS road_hazards (
    edge_id INTEGER NOT NULL,
    hazard_type VARCHAR(40) NOT NULL,
    ref_no VARCHAR(40) NOT NULL,
    risk VARCHAR(10),
    inside_ratio REAL NOT NULL DEFAULT 1,
    PRIMARY KEY (hazard_type, ref_no, edge_id)
);
CREATE INDEX IF NOT EXISTS idx_road_hazards_edge ON road_hazards (edge_id);

CREATE TABLE IF NOT EXISTS hazard_alerts (
    source VARCHAR(40) NOT NULL,
    hazard_type VARCHAR(40) NOT NULL,
    ref_no VARCHAR(40) NOT NULL,
    level VARCHAR(10) NOT NULL CHECK (level IN ('red', 'yellow')),
    county VARCHAR(20),
    town VARCHAR(20),
    village VARCHAR(40),
    issued_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, ref_no)
);

CREATE OR REPLACE VIEW road_closures AS
SELECT rh.edge_id,
       CASE a.level WHEN 'red' THEN 'closed' ELSE 'caution' END AS level,
       a.source, a.ref_no, a.issued_at, a.expires_at, rh.inside_ratio
FROM hazard_alerts a
JOIN road_hazards rh ON rh.hazard_type = a.hazard_type AND rh.ref_no = a.ref_no
WHERE a.expires_at > now();
