CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS shelters (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) UNIQUE,
    geom GEOMETRY(Point, 4326),
    capacity INTEGER,
    current_ppl INTEGER DEFAULT 0,
    address VARCHAR(200) DEFAULT ''
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
