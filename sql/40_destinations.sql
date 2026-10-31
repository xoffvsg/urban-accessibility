-- Destinations: service points + park access points, snapped to the nearest main edge and
-- attached to the graph as virtual nodes. Vars: schema, epsg, park_step_m, park_access_m.
SET search_path = :"schema", public;

-- 1. Destinations ------------------------------------------------------------------------------
-- group_key = the thing counted in n_400m (a park polygon counts once however many access points).
-- ref = service_points.ref (Overture id prefix / gtfs:<stop_id>), the id used in service_review.csv.
DROP TABLE IF EXISTS dests CASCADE;
CREATE TABLE dests (
    id bigserial PRIMARY KEY,
    ref text,
    service text NOT NULL,
    secondary boolean NOT NULL DEFAULT false,
    group_key text NOT NULL,
    name text,
    source text NOT NULL,
    geom geometry(Point, :epsg) NOT NULL
);

INSERT INTO dests (ref, service, secondary, group_key, name, source, geom)
SELECT s.ref, s.service, s.secondary, coalesce('park:' || pk.id, s.dest_key), s.name, s.source,
       s.geom
FROM service_points s
LEFT JOIN LATERAL (
    SELECT p.id FROM parks p
    WHERE s.service = 'parks' AND ST_Intersects(p.geom, s.geom) LIMIT 1
) pk ON true;

-- park polygons: boundary points every park_step_m that lie within park_access_m of the network
INSERT INTO dests (service, secondary, group_key, name, source, geom)
SELECT 'parks', false, 'park:' || p.id, p.name, 'landuse_access', d.geom
FROM parks p,
     ST_DumpPoints(ST_Segmentize(ST_Boundary(p.geom), :park_step_m)) d
WHERE EXISTS (
    SELECT 1 FROM edges e WHERE e.main AND ST_DWithin(e.geom, d.geom, :park_access_m)
);

-- 2. Snap to the nearest main edge (same side of any gated-area boundary) ----------------------
ALTER TABLE dests ADD COLUMN edge_id bigint, ADD COLUMN frac float8, ADD COLUMN snap_m float8,
                  ADD COLUMN node bigint;
UPDATE dests d SET edge_id = n.id,
                   frac = ST_LineLocatePoint(n.geom, d.geom),
                   snap_m = ST_Distance(n.geom, d.geom)
FROM dests d2
CROSS JOIN LATERAL (
    SELECT e.id, e.geom FROM edges e
    WHERE e.main AND e.gated IS NOT DISTINCT FROM (
        SELECT g.name FROM gated_areas g WHERE ST_Intersects(g.geom, d2.geom) LIMIT 1)
    ORDER BY e.geom <-> d2.geom LIMIT 1
) n
WHERE d2.id = d.id;

-- virtual node ids above every real vertex id
UPDATE dests SET node = id + (SELECT max(id) FROM vertices);
CREATE INDEX dests_service_idx ON dests (service);
CREATE INDEX dests_edge_idx ON dests (edge_id);
CREATE INDEX dests_geom_idx ON dests USING gist (geom);

-- 3. Virtual edges: node → both ends of its edge (cost ≥ the edge, so no shortcuts) -------------
DROP TABLE IF EXISTS dest_edges;
CREATE TABLE dest_edges AS
SELECT row_number() OVER ()::bigint + (SELECT max(id) FROM edges) AS id, service, source, target,
       cost
FROM (
    SELECT d.service, d.node AS source, e.source AS target,
           d.snap_m + d.frac * e.cost_m AS cost
    FROM dests d JOIN edges e ON e.id = d.edge_id
    UNION ALL
    SELECT d.service, d.node, e.target, d.snap_m + (1 - d.frac) * e.cost_m
    FROM dests d JOIN edges e ON e.id = d.edge_id
) x;
CREATE INDEX dest_edges_service_idx ON dest_edges (service);
ANALYZE dests; ANALYZE dest_edges;

\pset format unaligned
\pset tuples_only on
SELECT format('dests: %s', string_agg(format('%s %s', service, n), ', '))
FROM (SELECT service, count(*) n FROM dests GROUP BY service ORDER BY service) x;
SELECT format('parks: %s polygons, %s with network access points',
              (SELECT count(*) FROM parks),
              (SELECT count(DISTINCT group_key) FROM dests WHERE source = 'landuse_access'));
SELECT format('dest snaps > %s m: %s', :max_snap_m, count(*))
FROM dests WHERE snap_m > :max_snap_m;
