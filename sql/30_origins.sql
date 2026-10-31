-- Origins: residential buildings with dasymetric population, grid fallback, one centroid per
-- block group; each origin is snapped to the nearest main-component edge.
-- Vars: schema, epsg, min_area_m2, max_area_m2, floor_height_m, grid_m, m2_per_unit,
-- parcel_subtype_sql (boolean SQL on b.subtype), parcel_exclude_classes (quoted list), max_snap_m.
SET search_path = :"schema", public;

-- 1. Residential buildings ---------------------------------------------------------------------
-- 1a. Footprint heuristic (counties without a parcel source, and kept for comparison):
-- Overture residential classes (incl. static_caravan = mobile homes), or an unclassified footprint
-- of plausible house size that contains no non-residential place. Weight = area x floors.
DROP TABLE IF EXISTS res_heuristic CASCADE;
CREATE TABLE res_heuristic AS
WITH b AS (
    SELECT b.fid, b.id, b.subtype, b.class, b.height, b.num_floors, b.geom,
           ST_Area(b.geom) AS area_m2, ST_PointOnSurface(b.geom) AS pt
    FROM buildings b
), j AS (
    SELECT b.*, g.geoid AS bg_geoid
    FROM b JOIN block_groups g ON ST_Within(b.pt, g.geom)
), k AS (
    SELECT j.*,
           CASE
               WHEN (subtype = 'residential' AND class IS DISTINCT FROM 'garage'
                     AND class IS DISTINCT FROM 'garages')
                    OR class IN ('house', 'detached', 'semidetached_house', 'terrace',
                                 'apartments', 'residential', 'static_caravan', 'bungalow',
                                 'cabin', 'dormitory', 'farm', 'houseboat')
                   THEN 'res_class'
               WHEN subtype IS NULL AND class IS NULL
                    AND area_m2 BETWEEN :min_area_m2 AND :max_area_m2
                    AND NOT EXISTS (
                        SELECT 1 FROM places p
                        WHERE ST_Intersects(p.geom, j.geom)
                          AND p.operating_status IS DISTINCT FROM 'permanently_closed'
                          AND p.confidence >= 0.5
                          AND p.basic_category IS DISTINCT FROM 'housing_or_property_service'
                          AND p.basic_category IS DISTINCT FROM 'real_estate_service')
                   THEN 'unclassified'
           END AS kind
    FROM j
)
SELECT fid, id, subtype, class, kind, bg_geoid, area_m2,
       greatest(coalesce(num_floors::float8, greatest(1, round(height / :floor_height_m)), 1), 1)
           AS floors,
       pt AS geom
FROM k WHERE kind IS NOT NULL;
ALTER TABLE res_heuristic ADD COLUMN weight float8;
UPDATE res_heuristic SET weight = area_m2 * floors;

-- 1b. Parcel dwelling units (city config `homes:`, opendata.homes): units go to eligible buildings
-- (point-on-surface in the parcel, >= min_area_m2, residential or no subtype, not a garage/shed …).
-- assessor / one_per_parcel with 1 unit: all to the largest building; more units or floor_area:
-- spread by area x floors (floor_area units = floor area / m2_per_unit); assessor units with no
-- eligible building: one origin at the parcel's point-on-surface. Weight = dwelling units.
CREATE TABLE IF NOT EXISTS parcels (
    parcel_id text, county text, units float8, rule text, category text,
    geom geometry(MultiPolygon, :epsg)
);
DROP TABLE IF EXISTS res_parcel CASCADE;
CREATE TABLE res_parcel AS
WITH elig AS (
    SELECT b.id, b.subtype, b.class, ST_Area(b.geom) AS area_m2,
           greatest(coalesce(b.num_floors::float8,
                             greatest(1, round(b.height / :floor_height_m)), 1), 1) AS floors,
           ST_PointOnSurface(b.geom) AS pt
    FROM buildings b
    WHERE :parcel_subtype_sql
      AND coalesce(b.class, '') <> ALL (ARRAY[:parcel_exclude_classes]::text[])
      AND ST_Area(b.geom) >= :min_area_m2
), pb AS (
    SELECT p.parcel_id, p.county, p.units, p.rule, e.*,
           e.area_m2 * e.floors AS floor_m2,
           row_number() OVER (PARTITION BY p.parcel_id ORDER BY e.area_m2 * e.floors DESC, e.id)
               AS rnk,
           sum(e.area_m2 * e.floors) OVER (PARTITION BY p.parcel_id) AS parcel_floor_m2
    FROM parcels p JOIN elig e ON ST_Within(e.pt, p.geom)
    WHERE p.rule <> 'none' AND (p.units > 0 OR p.rule = 'floor_area')
), alloc AS (
    SELECT id, subtype, class, area_m2, floors, pt, parcel_id, county,
           CASE
               WHEN rule = 'floor_area'
                   THEN greatest(parcel_floor_m2 / :m2_per_unit, 1) * floor_m2 / parcel_floor_m2
               WHEN units <= 1 THEN CASE WHEN rnk = 1 THEN units ELSE 0 END
               ELSE units * floor_m2 / parcel_floor_m2
           END AS weight
    FROM pb
)
SELECT a.id, a.subtype, a.class, 'parcel_building'::text AS kind, g.geoid AS bg_geoid,
       a.area_m2, a.floors, a.pt AS geom, a.weight, a.parcel_id, a.county
FROM alloc a JOIN block_groups g ON ST_Within(a.pt, g.geom)
WHERE a.weight > 0
UNION ALL
SELECT NULL, NULL, NULL, 'parcel_point', g.geoid, NULL, NULL, x.pt, x.units, x.parcel_id, x.county
FROM (
    SELECT p.parcel_id, p.county, p.units, ST_PointOnSurface(p.geom) AS pt
    FROM parcels p
    WHERE p.rule = 'assessor' AND p.units > 0
      AND NOT EXISTS (SELECT 1 FROM elig e WHERE ST_Within(e.pt, p.geom))
) x JOIN block_groups g ON ST_Within(x.pt, g.geom);

-- 1c. Homes: parcel-based where the county has a parcel source, heuristic elsewhere
DROP TABLE IF EXISTS res_buildings CASCADE;
CREATE TABLE res_buildings AS
SELECT id, subtype, class, kind, bg_geoid, area_m2, floors, geom, weight, parcel_id
FROM res_parcel
UNION ALL
SELECT h.id, h.subtype, h.class, h.kind, h.bg_geoid, h.area_m2, h.floors, h.geom, h.weight, NULL
FROM res_heuristic h
WHERE left(h.bg_geoid, 5) NOT IN (SELECT DISTINCT county FROM parcels);

-- 2. Origins: buildings, grid fallback, centroids ----------------------------------------------
DROP TABLE IF EXISTS origins CASCADE;
CREATE TABLE origins (
    id bigserial PRIMARY KEY,
    kind text NOT NULL,                -- building | grid | centroid
    ref text,                          -- building id (parcel id for a parcel-point home)
    bg_geoid text NOT NULL,
    pop float8 NOT NULL DEFAULT 0,
    geom geometry(Point, :epsg) NOT NULL
);

INSERT INTO origins (kind, ref, bg_geoid, pop, geom)
SELECT 'building', coalesce(r.id, r.parcel_id), r.bg_geoid,
       g.pop_total * r.weight / sum(r.weight) OVER (PARTITION BY r.bg_geoid), r.geom
FROM res_buildings r JOIN block_groups g ON g.geoid = r.bg_geoid;

-- block groups with population but no residential building: regular point grid
INSERT INTO origins (kind, bg_geoid, pop, geom)
SELECT 'grid', x.geoid, x.pop_total / count(*) OVER (PARTITION BY x.geoid), x.pt
FROM (
    SELECT g.geoid, g.pop_total, ST_Centroid(sq.geom) AS pt
    FROM block_groups g, ST_SquareGrid(:grid_m, g.geom) sq
    WHERE g.pop_total > 0
      AND NOT EXISTS (SELECT 1 FROM res_buildings r WHERE r.bg_geoid = g.geoid)
      AND ST_Within(ST_Centroid(sq.geom), g.geom)
) x;

INSERT INTO origins (kind, bg_geoid, pop, geom)
SELECT 'centroid', geoid, 0, ST_PointOnSurface(geom) FROM block_groups;

-- 3. Snap to the nearest main-component edge on the same side of any gated-area boundary -------
ALTER TABLE origins ADD COLUMN edge_id bigint, ADD COLUMN frac float8, ADD COLUMN snap_m float8;
UPDATE origins o SET edge_id = n.id,
                     frac = ST_LineLocatePoint(n.geom, o.geom),
                     snap_m = ST_Distance(n.geom, o.geom)
FROM origins o2
CROSS JOIN LATERAL (
    SELECT e.id, e.geom FROM edges e
    WHERE e.main AND e.gated IS NOT DISTINCT FROM (
        SELECT g.name FROM gated_areas g WHERE ST_Intersects(g.geom, o2.geom) LIMIT 1)
    ORDER BY e.geom <-> o2.geom LIMIT 1
) n
WHERE o2.id = o.id;
CREATE INDEX origins_bg_idx ON origins (bg_geoid);
CREATE INDEX origins_edge_idx ON origins (edge_id);
CREATE INDEX origins_geom_idx ON origins USING gist (geom);
ANALYZE origins;

\pset format unaligned
\pset tuples_only on
SELECT format('homes: %s', string_agg(format('%s %s %s (units %s)', county, kind, n, round(w::numeric)), ', '))
FROM (SELECT left(bg_geoid, 5) AS county, kind, count(*) n, sum(weight) w
      FROM res_buildings GROUP BY 1, 2 ORDER BY 1, 2) x;
SELECT format('origins: %s', string_agg(format('%s %s (pop %s)', kind, n, round(p::numeric)), ', '))
FROM (SELECT kind, count(*) n, sum(pop) p FROM origins GROUP BY kind ORDER BY kind) x;
SELECT format('origin snaps > %s m: %s', :max_snap_m, count(*))
FROM origins WHERE snap_m > :max_snap_m;
