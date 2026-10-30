-- Independent distance check (run via network.check_pairs, SQLAlchemy params :n).
-- For n random building origins and services: insert the origin as its own virtual node
-- (two edges to its edge's ends), run point-to-many pgr_dijkstraCost to every destination of
-- the service, take the minimum, and compare with origin_dist.dist_m.
WITH pick AS (
    SELECT od.origin_id, od.service, od.dist_m
    FROM __SCHEMA__.origin_dist od JOIN __SCHEMA__.origins o ON o.id = od.origin_id
    WHERE o.kind = 'building' AND od.dist_m IS NOT NULL
    ORDER BY random() LIMIT :n
)
SELECT p.origin_id, p.service, p.dist_m, chk.d AS dijkstra_m, chk.d - p.dist_m AS diff_m
FROM pick p
CROSS JOIN LATERAL (
    SELECT min(c.agg_cost) AS d
    FROM pgr_dijkstraCost(
        format($q$
            SELECT id, source, target, cost_m AS cost FROM __SCHEMA__.edges WHERE main
            UNION ALL
            SELECT id, source, target, cost FROM __SCHEMA__.dest_edges WHERE service = %1$L
            UNION ALL
            SELECT -1, -1, e.source, o.snap_m + o.frac * e.cost_m
            FROM __SCHEMA__.origins o JOIN __SCHEMA__.edges e ON e.id = o.edge_id WHERE o.id = %2$s
            UNION ALL
            SELECT -2, -1, e.target, o.snap_m + (1 - o.frac) * e.cost_m
            FROM __SCHEMA__.origins o JOIN __SCHEMA__.edges e ON e.id = o.edge_id WHERE o.id = %2$s
        $q$, p.service, p.origin_id),
        -1::bigint,
        (SELECT array_agg(node) FROM __SCHEMA__.dests WHERE service = p.service),
        false) c
) chk;
