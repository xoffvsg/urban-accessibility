"""Extract Overture themes for a city extent from S3 (DuckDB) → GeoParquet in data/raw/<slug>/."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .config import BBox, CityConfig

S3_ROOT = "s3://overturemaps-us-west-2/release"


def duckdb_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    return con


def overture_path(release: str, theme: str, type_: str) -> str:
    return f"{S3_ROOT}/{release}/theme={theme}/type={type_}/*"


@dataclass(frozen=True)
class Extract:
    """One Overture output file: what to select from which theme/type."""

    out_name: str
    theme: str
    type_: str
    columns_sql: str
    where_sql: str = "TRUE"


# Nested structs are written as JSON text (`*_json`) so ogr2ogr / PostGIS can load them as jsonb.
EXTRACTS: dict[str, Extract] = {
    "places": Extract(
        "places",
        "places",
        "place",
        """id, names.primary AS name, basic_category,
           taxonomy.primary AS taxonomy_primary,
           taxonomy.hierarchy AS taxonomy_hierarchy,
           taxonomy.alternates AS taxonomy_alternates,
           brand.names.primary AS brand, confidence, operating_status, geometry""",
    ),
    "buildings": Extract(
        "buildings",
        "buildings",
        "building",
        """id, names.primary AS name, subtype, class, height, num_floors, geometry""",
    ),
    "segments": Extract(
        "segments",
        "transportation",
        "segment",
        """id, names.primary AS name, subtype, class, subclass,
           to_json(connectors) AS connectors_json,
           to_json(road_flags) AS road_flags_json,
           to_json(road_surface) AS road_surface_json,
           to_json(access_restrictions) AS access_restrictions_json,
           to_json(level_rules) AS level_rules_json,
           to_json(subclass_rules) AS subclass_rules_json,
           geometry""",
        "subtype = 'road'",
    ),
    "connectors": Extract("connectors", "transportation", "connector", "id, geometry"),
    "neighborhoods": Extract(
        "neighborhoods",
        "divisions",
        "division_area",
        "id, division_id, names.primary AS name, subtype, class, geometry",
        "subtype IN ('neighborhood', 'microhood')",
    ),
}


def parks_extract(landuse: dict[str, tuple[str, ...]]) -> Extract:
    """Park polygons from base/land_use, filtered by the (subtype → classes) map in scoring.yaml."""
    clauses = [
        f"(subtype = '{sub}' AND class IN ({', '.join(repr(c) for c in classes)}))"
        for sub, classes in landuse.items()
    ]
    where = "(" + " OR ".join(clauses or ["FALSE"]) + ")"
    where += " AND ST_GeometryType(geometry) IN ('POLYGON', 'MULTIPOLYGON')"
    return Extract(
        "parks", "base", "land_use", "id, names.primary AS name, subtype, class, geometry", where
    )


def run_extract(
    con: duckdb.DuckDBPyConnection,
    ex: Extract,
    release: str,
    extent: BBox,
    out_dir: Path,
    force: bool = False,
) -> Path:
    """Write <out_dir>/<name>.parquet (GeoParquet, WGS84) + <name>.meta.json; return the path."""
    out = out_dir / f"{ex.out_name}.parquet"
    if out.exists() and not force:
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    # filter on the bbox struct first: parquet row-group stats make this cheap
    sql = f"""
        COPY (
            SELECT {ex.columns_sql}
            FROM read_parquet(?, hive_partitioning = 1)
            WHERE bbox.xmin <= ? AND bbox.xmax >= ? AND bbox.ymin <= ? AND bbox.ymax >= ?
              AND ({ex.where_sql})
        ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)
    """
    t0 = time.time()
    con.execute(
        sql,
        [
            overture_path(release, ex.theme, ex.type_),
            extent.lon_max,
            extent.lon_min,
            extent.lat_max,
            extent.lat_min,
        ],
    )
    tmp.replace(out)
    n = con.execute("SELECT count(*) FROM read_parquet(?)", [str(out)]).fetchone()[0]
    meta = {
        "source": f"overture:{ex.theme}/{ex.type_}",
        "release": release,
        "extent_wgs84": extent.as_tuple(),
        "filter": " ".join(ex.where_sql.split()),
        "rows": n,
        "seconds": round(time.time() - t0, 1),
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
    return out


def extract_all(
    cfg: CityConfig, extent: BBox, only: list[str] | None = None, force: bool = False
) -> dict[str, Path]:
    """Run every Overture extract (or the subset `only`) for `extent`."""
    extracts = dict(EXTRACTS)
    extracts["parks"] = parks_extract(cfg.scoring.services["parks"].landuse)
    names = only or list(extracts)
    unknown = set(names) - set(extracts)
    if unknown:
        raise ValueError(f"unknown extracts: {sorted(unknown)}; choose from {sorted(extracts)}")
    con = duckdb_connection()
    out: dict[str, Path] = {}
    for name in names:
        print(f"[extract] {name} …", flush=True)
        out[name] = run_extract(
            con, extracts[name], cfg.overture_release, extent, cfg.raw_dir, force
        )
    return out
