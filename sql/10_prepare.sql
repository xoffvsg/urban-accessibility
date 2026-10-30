-- After ogr2ogr: repair geometries, cast JSON text to jsonb, index, analyse.
SET search_path = :"schema", public;

UPDATE buildings SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))
WHERE NOT ST_IsValid(geom);
UPDATE parks SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))
WHERE NOT ST_IsValid(geom);
UPDATE block_groups SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))
WHERE NOT ST_IsValid(geom);

ALTER TABLE segments
    ALTER COLUMN connectors_json TYPE jsonb USING connectors_json::jsonb,
    ALTER COLUMN access_restrictions_json TYPE jsonb USING access_restrictions_json::jsonb,
    ALTER COLUMN road_flags_json TYPE jsonb USING road_flags_json::jsonb,
    ALTER COLUMN road_surface_json TYPE jsonb USING road_surface_json::jsonb,
    ALTER COLUMN level_rules_json TYPE jsonb USING level_rules_json::jsonb,
    ALTER COLUMN subclass_rules_json TYPE jsonb USING subclass_rules_json::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS block_groups_geoid_idx ON block_groups (geoid);
CREATE INDEX IF NOT EXISTS service_points_service_idx ON service_points (service);

ANALYZE places; ANALYZE buildings; ANALYZE segments; ANALYZE connectors; ANALYZE parks;
ANALYZE block_groups; ANALYZE service_points;
