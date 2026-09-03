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
