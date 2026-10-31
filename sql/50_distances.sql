-- Network distances origin → nearest destination per service, service counts within reach, and
-- 400 m service areas. Vars: schema, max_dist_m (last band), near_m (first band),
-- people_radius_m (radius for people_per_grocery), sa_buffer_m.
SET search_path = :"schema", public;
-- psql variables don't reach inside DO blocks; pass them as session settings
SET ua.max_dist_m = :'max_dist_m';
SET ua.near_m = :'near_m';
SET ua.people_radius_m = :'people_radius_m';

-- 1. Nearest destination per vertex -----------------------------------------------------------
-- A virtual super-source joins every destination node of the service at cost 0; single-source
-- pgr_drivingDistance from it gives the exact minimum over destinations. (Multi-source
-- equicost := true does NOT: in pgRouting 4.0.1 it returned 506 m for a vertex whose nearest stop
-- is 453 m away.) The nearest destination is the root of each node's predecessor chain.
DROP TABLE IF EXISTS vertex_dist;
CREATE TABLE vertex_dist (service text, node bigint, dest_node bigint, agg_cost float8);
DO $$
DECLARE s text; src bigint;
BEGIN
    src := (SELECT max(node) + 1 FROM dests);
    FOR s IN SELECT DISTINCT service FROM dests ORDER BY 1 LOOP
        CREATE TEMP TABLE dd AS
        SELECT node, pred, agg_cost
        FROM pgr_drivingDistance(
            format($q$SELECT id, source, target, cost_m AS cost FROM %1$I.edges WHERE main
                      UNION ALL
                      SELECT id, source, target, cost FROM %1$I.dest_edges WHERE service = %2$L
                      UNION ALL
                      SELECT -id, %3$s, node, 0 FROM %1$I.dests WHERE service = %2$L$q$,
                   current_schema(), s, src),
            src, current_setting('ua.max_dist_m')::float8, false);
        INSERT INTO vertex_dist
        WITH RECURSIVE t AS (
            SELECT node, node AS root FROM dd WHERE pred = src AND node <> src
            UNION ALL
            SELECT d.node, t.root FROM dd d JOIN t ON d.pred = t.node WHERE d.node <> t.node
        )
        SELECT s, t.node, t.root, dd.agg_cost FROM t JOIN dd USING (node);
        DROP TABLE dd;
    END LOOP;
END $$;
CREATE INDEX vertex_dist_idx ON vertex_dist (service, node);
ANALYZE vertex_dist;

-- 2. Origin distances ---------------------------------------------------------------------------
-- via an end of the origin's edge, or directly along the edge when a destination shares it
DROP TABLE IF EXISTS origin_dist;
CREATE TABLE origin_dist AS
SELECT o.id AS origin_id, s.service, best.d AS dist_m, dn.id AS nearest_dest_id, eu.d AS euclid_m
FROM origins o
JOIN edges e ON e.id = o.edge_id
CROSS JOIN (SELECT DISTINCT service FROM dests) s
LEFT JOIN vertex_dist vs ON vs.service = s.service AND vs.node = e.source
LEFT JOIN vertex_dist vt ON vt.service = s.service AND vt.node = e.target
LEFT JOIN LATERAL (
    SELECT o.snap_m + d.snap_m + abs(d.frac - o.frac) * e.cost_m AS d, d.node
    FROM dests d WHERE d.edge_id = o.edge_id AND d.service = s.service
    ORDER BY 1 LIMIT 1
) same ON true
LEFT JOIN LATERAL (
    SELECT v.d, v.node FROM (VALUES
        (o.snap_m + vs.agg_cost + o.frac * e.cost_m, vs.dest_node),
        (o.snap_m + vt.agg_cost + (1 - o.frac) * e.cost_m, vt.dest_node),
        (same.d, same.node)) v(d, node)
    WHERE v.d IS NOT NULL ORDER BY v.d LIMIT 1
) best ON true
LEFT JOIN dests dn ON dn.node = best.node
CROSS JOIN LATERAL (
    SELECT ST_Distance(d.geom, o.geom) AS d FROM dests d
    WHERE d.service = s.service ORDER BY d.geom <-> o.geom LIMIT 1
) eu;
CREATE INDEX origin_dist_idx ON origin_dist (origin_id, service);
ANALYZE origin_dist;

-- 3. Destinations within reach of each vertex (one row per counted destination) ---------------
-- radius: first band for every service, people_radius_m for grocery (people_per_grocery).
-- Counts need "is this destination (group_key) within r?", not which access point, so each group
-- gets one virtual source node with one-way, zero-cost links to its access points: one search per
-- group instead of one per point (parks: ~117 parks vs ~4,300 boundary points, same result). The
-- links are one-way (reverse_cost -1, directed search; street edges get equal costs both ways) so a
-- route can never enter another group's source node and cross that park for free.
DROP TABLE IF EXISTS group_src;
CREATE TABLE group_src AS
SELECT service, group_key,
       (SELECT max(node) FROM dests) + dense_rank() OVER (ORDER BY service, group_key) AS gnode
FROM (SELECT DISTINCT service, group_key FROM dests) x;
DROP TABLE IF EXISTS group_links;
CREATE TABLE group_links AS
SELECT (SELECT max(id) FROM dest_edges) + row_number() OVER (ORDER BY g.gnode, d.node) AS id,
       d.service, g.gnode AS source, d.node AS target
FROM dests d JOIN group_src g USING (service, group_key);
CREATE INDEX group_links_service_idx ON group_links (service);

DROP TABLE IF EXISTS vertex_reach;
CREATE TABLE vertex_reach (service text, node bigint, group_key text, agg_cost float8);
DO $$
DECLARE s text; r float8;
BEGIN
    FOR s IN SELECT DISTINCT service FROM dests ORDER BY 1 LOOP
        r := CASE WHEN s = 'grocery' THEN current_setting('ua.people_radius_m')::float8
                  ELSE current_setting('ua.near_m')::float8 END;
        INSERT INTO vertex_reach
        SELECT s, dd.node, g.group_key, dd.agg_cost
        FROM pgr_drivingDistance(
            format($q$SELECT id, source, target, cost_m AS cost, cost_m AS reverse_cost
                      FROM %1$I.edges WHERE main
                      UNION ALL
                      SELECT id, source, target, cost, cost FROM %1$I.dest_edges
                      WHERE service = %2$L
                      UNION ALL
                      SELECT id, source, target, 0, -1 FROM %1$I.group_links
                      WHERE service = %2$L$q$,
                   current_schema(), s),
            (SELECT array_agg(gnode) FROM group_src WHERE service = s), r, true, false) dd
        JOIN group_src g ON g.gnode = dd.start_vid AND g.service = s;
    END LOOP;
END $$;
CREATE INDEX vertex_reach_idx ON vertex_reach (node);
ANALYZE vertex_reach;

-- distinct destinations (group_key) reachable from any residence of a block group
DROP TABLE IF EXISTS bg_counts;
CREATE TABLE bg_counts AS
WITH o AS (
    SELECT o.id, o.bg_geoid, o.snap_m, o.frac, o.edge_id, e.source, e.target, e.cost_m
    FROM origins o JOIN edges e ON e.id = o.edge_id
    WHERE o.kind <> 'centroid'
), reach AS (
    SELECT o.bg_geoid, r.service, r.group_key, o.snap_m + r.agg_cost + o.frac * o.cost_m AS d
    FROM o JOIN vertex_reach r ON r.node = o.source
    UNION ALL
    SELECT o.bg_geoid, r.service, r.group_key, o.snap_m + r.agg_cost + (1 - o.frac) * o.cost_m
    FROM o JOIN vertex_reach r ON r.node = o.target
    UNION ALL
    SELECT o.bg_geoid, d.service, d.group_key, o.snap_m + d.snap_m + abs(d.frac - o.frac) * o.cost_m
    FROM o JOIN dests d ON d.edge_id = o.edge_id
)
SELECT bg_geoid, service,
       count(DISTINCT group_key) FILTER (WHERE d <= current_setting('ua.near_m')::float8)
           AS n_near,
       count(DISTINCT group_key) FILTER (WHERE d <= current_setting('ua.people_radius_m')::float8)
           AS n_people_radius
FROM reach GROUP BY bg_geoid, service;

-- 4. Service areas within the first band: reached portions of edges, buffered -------------------
DROP TABLE IF EXISTS service_areas;
CREATE TABLE service_areas AS
WITH ed AS (
    SELECT s.service, e.geom, e.cost_m,
           coalesce(vs.agg_cost, 'infinity') AS ds, coalesce(vt.agg_cost, 'infinity') AS dt
    FROM edges e CROSS JOIN (SELECT DISTINCT service FROM dests) s
    LEFT JOIN vertex_dist vs ON vs.service = s.service AND vs.node = e.source
    LEFT JOIN vertex_dist vt ON vt.service = s.service AND vt.node = e.target
    WHERE e.main
), portions AS (
    SELECT service,
           CASE WHEN ds + cost_m <= :near_m OR dt + cost_m <= :near_m
                     OR (ds <= :near_m AND dt <= :near_m) THEN geom
                WHEN ds <= :near_m
                    THEN ST_LineSubstring(geom, 0, least(1, (:near_m - ds) / cost_m))
                WHEN dt <= :near_m
                    THEN ST_LineSubstring(geom, greatest(0, 1 - (:near_m - dt) / cost_m), 1)
           END AS geom
    FROM ed WHERE ds <= :near_m OR dt <= :near_m
)
-- union of per-edge buffers: ~60x faster than buffering the collected lines, same area
SELECT service, ST_Multi(ST_Union(ST_Buffer(geom, :sa_buffer_m, 'quad_segs=4'))) AS geom
FROM portions GROUP BY service;

DROP TABLE IF EXISTS bg_cover;
CREATE TABLE bg_cover AS
SELECT g.geoid AS bg_geoid, sa.service,
       100 * ST_Area(ST_Intersection(g.geom, sa.geom)) / ST_Area(g.geom) AS cover_pct
FROM block_groups g CROSS JOIN service_areas sa;

\pset format unaligned
\pset tuples_only on
SELECT format('origin_dist rows %s; within %s m: %s%%', count(*), :'max_dist_m',
              round(100.0 * count(dist_m) / count(*), 1))
FROM origin_dist;
SELECT format('vertex_reach rows %s; bg_counts rows %s', (SELECT count(*) FROM vertex_reach),
              (SELECT count(*) FROM bg_counts));
