-- Walk network: walkable segments → split at Overture connectors → edges/vertices → components.
-- Vars: schema. Distances in metres (analysis CRS). pgRouting 4: no pgr_createTopology.
SET search_path = :"schema", public;

-- 1. Walkable segments --------------------------------------------------------------------------
-- Drop motorways. Drop a segment when a rule denies foot travel (no heading; mode absent or
-- containing foot) and no rule allows it (allowed/designated for foot or all modes, incl. as_private).
-- Also drop segments listed in the city's blocked_segments: (fences, gates; Overture has no barriers).
DROP TABLE IF EXISTS walk_segments CASCADE;
CREATE TABLE walk_segments AS
WITH r AS (
    SELECT s.fid,
           e->>'access_type' AS access_type,
           coalesce(jsonb_typeof(e->'when'->'mode'), 'null') = 'null'
               OR (e->'when'->'mode') ? 'foot' AS applies_to_foot,
           (e->'when'->>'heading') IS NULL AS both_ways
    FROM segments s, jsonb_array_elements(s.access_restrictions_json) e
    WHERE jsonb_typeof(s.access_restrictions_json) = 'array'
), foot AS (
    SELECT fid,
           bool_or(access_type = 'denied' AND applies_to_foot AND both_ways) AS denied,
           bool_or(access_type IN ('allowed', 'designated') AND applies_to_foot AND both_ways)
               AS allowed
    FROM r GROUP BY fid
)
SELECT s.fid, s.id AS segment_id, s.class, s.subclass, s.name, s.connectors_json,
       NOT coalesce(f.denied AND NOT f.allowed, false) AND b.segment_id IS NULL AS foot_ok,
       b.note AS blocked_note, s.geom
FROM segments s LEFT JOIN foot f USING (fid)
LEFT JOIN blocked_segments b ON b.segment_id = s.id;

-- 2. Split at connectors ------------------------------------------------------------------------
DROP TABLE IF EXISTS edges_raw;
CREATE TABLE edges_raw AS
WITH c AS (
    SELECT w.segment_id, w.class, w.subclass, w.geom, e.ord,
           e.val->>'connector_id' AS cid, (e.val->>'at')::float8 AS at
    FROM walk_segments w,
         jsonb_array_elements(w.connectors_json) WITH ORDINALITY AS e(val, ord)
    WHERE w.foot_ok AND w.class IS DISTINCT FROM 'motorway'
), p AS (
    SELECT segment_id, class, subclass, geom, cid AS s_cid, at AS s_at,
           lead(cid) OVER w AS t_cid, lead(at) OVER w AS t_at
    FROM c WINDOW w AS (PARTITION BY segment_id ORDER BY at, ord)
)
SELECT segment_id, class, subclass, s_cid, t_cid,
       ST_LineSubstring(geom, s_at, t_at) AS geom
FROM p
WHERE t_cid IS NOT NULL AND t_at > s_at AND s_cid <> t_cid;

DROP TABLE IF EXISTS vertices CASCADE;
CREATE TABLE vertices AS
SELECT row_number() OVER (ORDER BY cid)::bigint AS id, cid AS connector_id
FROM (SELECT s_cid AS cid FROM edges_raw UNION SELECT t_cid FROM edges_raw) x;
ALTER TABLE vertices ADD PRIMARY KEY (id);
CREATE UNIQUE INDEX vertices_cid_idx ON vertices (connector_id);

DROP TABLE IF EXISTS edges CASCADE;
CREATE TABLE edges AS
SELECT row_number() OVER (ORDER BY e.segment_id, e.s_cid)::bigint AS id,
       vs.id AS source, vt.id AS target,
       ST_Length(e.geom) AS cost_m,
       e.segment_id, e.class, e.subclass, e.geom
FROM edges_raw e
JOIN vertices vs ON vs.connector_id = e.s_cid
JOIN vertices vt ON vt.connector_id = e.t_cid
WHERE ST_Length(e.geom) > 0;
ALTER TABLE edges ADD PRIMARY KEY (id);
DROP TABLE edges_raw;

-- closed gates: lines drawn across a gate (city config `gates:`, status closed) remove the
-- edges they cross; OSM gates explicitly closed to walkers (osm_gates.closed: access=no / foot=no)
-- remove the edge they lie on (within 1.5 m), unless they sit at an edge end (a junction: ambiguous)
CREATE TABLE IF NOT EXISTS osm_gates (osm_id text, closed boolean, geom geometry(Point, :epsg));
DELETE FROM edges e USING gates g
WHERE g.status = 'closed' AND ST_Intersects(e.geom, g.geom);
DELETE FROM edges e USING osm_gates b
WHERE b.closed AND ST_DWithin(e.geom, b.geom, 1.5)
  AND ST_Distance(ST_StartPoint(e.geom), b.geom) > 1.5
  AND ST_Distance(ST_EndPoint(e.geom), b.geom) > 1.5;

-- gated areas (city config): drop edges crossing a boundary unless the street is a listed exit;
-- tag edges inside so origins/destinations snap on their own side of the fence
DELETE FROM edges e
USING gated_areas g, walk_segments w
WHERE w.segment_id = e.segment_id
  AND ST_Intersects(e.geom, ST_Boundary(g.geom))
  AND NOT coalesce(w.name = ANY (string_to_array(g.exits, ';')), false);

-- open gates: each end of the drawn line becomes a vertex splitting its nearest remaining edge
-- (two partial edges, like destinations; the original edge stays), and the line itself becomes
-- an edge between them (cost = line length + both snaps). Added after the gated-area cut, so a
-- gate is an exit wherever it is drawn.
CREATE TEMP TABLE gate_ends AS
SELECT g.id AS gate_id, x.end_no, n.id AS edge_id, n.source, n.target, n.cost_m, n.geom AS egeom,
       ST_LineLocatePoint(n.geom, x.pt) AS frac, ST_Distance(n.geom, x.pt) AS snap_m,
       (SELECT max(id) FROM vertices) + 2 * g.id - 2 + x.end_no AS vid
FROM gates g
CROSS JOIN LATERAL (VALUES (1, ST_StartPoint(g.geom)), (2, ST_EndPoint(g.geom))) x(end_no, pt)
CROSS JOIN LATERAL (SELECT e.* FROM edges e ORDER BY e.geom <-> x.pt LIMIT 1) n
WHERE g.status = 'open';
INSERT INTO vertices (id, connector_id)  -- geom: filled from edge ends below
SELECT vid, format('gate:%s:%s', gate_id, end_no) FROM gate_ends;
INSERT INTO edges (id, source, target, cost_m, segment_id, class, subclass, geom)
SELECT (SELECT max(id) FROM edges) + row_number() OVER (ORDER BY gate_id, end_no, part),
       src, tgt, cost, format('gate:%s', gate_id), 'gate', 'split', geom
FROM (
    SELECT gate_id, end_no, 1 AS part, source AS src, vid AS tgt, frac * cost_m AS cost,
           ST_LineSubstring(egeom, 0, frac) AS geom FROM gate_ends WHERE frac > 0
    UNION ALL
    SELECT gate_id, end_no, 2, vid, target, (1 - frac) * cost_m,
           ST_LineSubstring(egeom, frac, 1) FROM gate_ends WHERE frac < 1
) parts;
INSERT INTO edges (id, source, target, cost_m, segment_id, class, subclass, geom)
SELECT (SELECT max(id) FROM edges) + row_number() OVER (ORDER BY g.id), a.vid, b.vid,
       ST_Length(g.geom) + a.snap_m + b.snap_m, format('gate:%s', g.id), 'gate', 'link', g.geom
FROM gates g
JOIN gate_ends a ON a.gate_id = g.id AND a.end_no = 1
JOIN gate_ends b ON b.gate_id = g.id AND b.end_no = 2;

ALTER TABLE edges ADD COLUMN gated text;
UPDATE edges e SET gated = g.name
FROM gated_areas g WHERE ST_Intersects(g.geom, ST_LineInterpolatePoint(e.geom, 0.5));
DELETE FROM vertices v
WHERE NOT EXISTS (SELECT 1 FROM edges e WHERE v.id IN (e.source, e.target));

-- vertex geometry: the connector point (falls back to the edge end point)
ALTER TABLE vertices ADD COLUMN geom geometry(Point, :epsg);
UPDATE vertices v SET geom = c.geom FROM connectors c WHERE c.id = v.connector_id;
UPDATE vertices v SET geom = ST_StartPoint(e.geom)
FROM edges e WHERE v.geom IS NULL AND e.source = v.id;
UPDATE vertices v SET geom = ST_EndPoint(e.geom)
FROM edges e WHERE v.geom IS NULL AND e.target = v.id;

-- 3. Connected components; routing uses only the largest ----------------------------------------
ALTER TABLE vertices ADD COLUMN component bigint;
UPDATE vertices v SET component = c.component
FROM pgr_connectedComponents(
    format('SELECT id, source, target, cost_m AS cost, cost_m AS reverse_cost FROM %I.edges',
           current_schema())
) c
WHERE c.node = v.id;

ALTER TABLE edges ADD COLUMN main boolean;
UPDATE edges e SET main = (v.component = m.component)
FROM vertices v,
     (SELECT component FROM vertices GROUP BY component ORDER BY count(*) DESC LIMIT 1) m
WHERE v.id = e.source;

CREATE INDEX edges_geom_idx ON edges USING gist (geom);
CREATE INDEX edges_main_geom_idx ON edges USING gist (geom) WHERE main;
CREATE INDEX edges_source_idx ON edges (source);
CREATE INDEX edges_target_idx ON edges (target);
CREATE INDEX vertices_geom_idx ON vertices USING gist (geom);
ANALYZE walk_segments; ANALYZE edges; ANALYZE vertices;

-- summary (printed by db.run_sql)
\pset format unaligned
\pset tuples_only on
SELECT format('segments %s, walkable %s (motorway %s, foot-denied %s)',
              count(*), count(*) FILTER (WHERE foot_ok AND class IS DISTINCT FROM 'motorway'),
              count(*) FILTER (WHERE class = 'motorway'), count(*) FILTER (WHERE NOT foot_ok))
FROM walk_segments;
SELECT format('edges %s, vertices %s, components %s, main-component edges %s (%s%% of length)',
              (SELECT count(*) FROM edges), (SELECT count(*) FROM vertices),
              (SELECT count(DISTINCT component) FROM vertices),
              count(*) FILTER (WHERE main),
              round((100 * sum(cost_m) FILTER (WHERE main) / sum(cost_m))::numeric, 2))
FROM edges;
