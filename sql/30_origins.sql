-- Origins: residential buildings with dasymetric population, grid fallback, one centroid per
-- block group; each origin is snapped to the nearest main-component edge.
-- Vars: schema, epsg, min_area_m2, max_area_m2, floor_height_m, grid_m.
SET search_path = :"schema", public;

-- 1. Residential buildings ---------------------------------------------------------------------
-- Residential = Overture residential classes (incl. static_caravan = mobile homes), or an
-- unclassified footprint of plausible house size that contains no non-residential place.
DROP TABLE IF EXISTS res_buildings CASCADE;
CREATE TABLE res_buildings AS
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
       coalesce(num_floors::float8,
                greatest(1, round(height / :floor_height_m)),
                1) AS floors,
       pt AS geom
FROM k WHERE kind IS NOT NULL;
UPDATE res_buildings SET floors = greatest(floors, 1);
ALTER TABLE res_buildings ADD COLUMN weight float8;
UPDATE res_buildings SET weight = area_m2 * floors;

-- 2. Origins: buildings, grid fallback, centroids ----------------------------------------------
DROP TABLE IF EXISTS origins CASCADE;
CREATE TABLE origins (
    id bigserial PRIMARY KEY,
    kind text NOT NULL,                -- building | grid | centroid
    ref text,                          -- building id
    bg_geoid text NOT NULL,
    pop float8 NOT NULL DEFAULT 0,
    geom geometry(Point, :epsg) NOT NULL
);

INSERT INTO origins (kind, ref, bg_geoid, pop, geom)
SELECT 'building', r.id, r.bg_geoid,
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
SELECT format('residential buildings %s (classified %s, unclassified %s)', count(*),
              count(*) FILTER (WHERE kind = 'res_class'),
              count(*) FILTER (WHERE kind = 'unclassified'))
FROM res_buildings;
SELECT format('origins: %s', string_agg(format('%s %s (pop %s)', kind, n, round(p::numeric)), ', '))
FROM (SELECT kind, count(*) n, sum(pop) p FROM origins GROUP BY kind ORDER BY kind) x;
SELECT format('origin snaps > %s m: %s', :max_snap_m, count(*))
FROM origins WHERE snap_m > :max_snap_m;
