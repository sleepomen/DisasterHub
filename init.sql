CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS roads (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    geom GEOMETRY(LineString, 4326),  
    base_weight FLOAT DEFAULT 1.0,   
    current_weight FLOAT DEFAULT 1.0, 
    is_simulated BOOLEAN DEFAULT FALSE 
);

CREATE TABLE IF NOT EXISTS shelters (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) UNIQUE,
    geom GEOMETRY(Point, 4326),      
    capacity INTEGER,             
    current_ppl INTEGER DEFAULT 0
);
