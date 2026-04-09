CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS roads (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    geom GEOMETRY(LineString, 4326),  
    base_weight FLOAT DEFAULT 1.0,   
    current_weight FLOAT DEFAULT 1.0, 
    is_simulated BOOLEAN DEFAULT FALSE 
);

-- shelters：【專案改動】name 加上 UNIQUE 且長度 200，與 ShelterRepository UPSERT 之 ON CONFLICT (name)
-- 一致，並容納 [OPENDATA:…] 前綴之較長名稱。若既有資料庫無 UNIQUE，需自行 migration。
CREATE TABLE IF NOT EXISTS shelters (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) UNIQUE,
    geom GEOMETRY(Point, 4326),      
    capacity INTEGER,             
    current_ppl INTEGER DEFAULT 0
);
