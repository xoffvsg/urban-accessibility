"""Load and validate city + scoring configuration; derive CRS, extents and paths."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pyproj import Transformer

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

load_dotenv(REPO_ROOT / ".env")


class ConfigError(ValueError):
    """Raised when a config file is missing values or inconsistent."""


@dataclass(frozen=True)
class BBox:
    """WGS84 bounding box in degrees."""

    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

    def __post_init__(self) -> None:
        if not (-180 <= self.lon_min < self.lon_max <= 180):
            raise ConfigError(f"bad longitude range {self.lon_min}..{self.lon_max}")
        if not (-90 <= self.lat_min < self.lat_max <= 90):
            raise ConfigError(f"bad latitude range {self.lat_min}..{self.lat_max}")

    @property
    def centroid(self) -> tuple[float, float]:
        return ((self.lon_min + self.lon_max) / 2, (self.lat_min + self.lat_max) / 2)

    def as_tuple(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) — the order shapely / geopandas use."""
        return (self.lon_min, self.lat_min, self.lon_max, self.lat_max)

    def union(self, other: BBox) -> BBox:
        return BBox(
            min(self.lon_min, other.lon_min),
            min(self.lat_min, other.lat_min),
            max(self.lon_max, other.lon_max),
            max(self.lat_max, other.lat_max),
        )

    def buffered(self, metres: float, epsg: int) -> BBox:
        """Expand by at least `metres` in the projected CRS `epsg`; return the WGS84 envelope.

        Envelope-of-envelope: slightly larger than `metres` where the UTM grid is rotated.
        """
        fwd = Transformer.from_crs(4326, epsg, always_xy=True)
        inv = Transformer.from_crs(epsg, 4326, always_xy=True)
        # densify the edges so the projected envelope covers the curved outline
        xs, ys = [], []
        for i in range(21):
            t = i / 20
            lon = self.lon_min + t * (self.lon_max - self.lon_min)
            lat = self.lat_min + t * (self.lat_max - self.lat_min)
            for x, y in (
                (lon, self.lat_min),
                (lon, self.lat_max),
                (self.lon_min, lat),
                (self.lon_max, lat),
            ):
                px, py = fwd.transform(x, y)
                xs.append(px)
                ys.append(py)
        corners = [
            (min(xs) - metres, min(ys) - metres),
            (min(xs) - metres, max(ys) + metres),
            (max(xs) + metres, min(ys) - metres),
            (max(xs) + metres, max(ys) + metres),
        ]
        lons, lats = zip(*(inv.transform(x, y) for x, y in corners), strict=True)
        return BBox(min(lons), min(lats), max(lons), max(lats))


def utm_epsg(lon: float, lat: float) -> int:
    """WGS84 UTM zone EPSG code for a point (326xx north, 327xx south)."""
    zone = int(math.floor((lon + 180) / 6)) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@dataclass(frozen=True)
class ServiceRule:
    """How Overture places map to one service type."""

    name: str
    taxonomy: tuple[str, ...] = ()
    min_confidence: float = 0.5
    include_names: tuple[str, ...] = ()
    exclude_names: tuple[str, ...] = ()
    landuse: dict[str, tuple[str, ...]] = field(default_factory=dict)
    secondary: dict[str, tuple[str, ...]] = field(default_factory=dict)
    prefer_gtfs: bool = False


@dataclass(frozen=True)
class Scoring:
    walk_speed_m_per_min: float
    bands: tuple[tuple[float, float], ...]  # (max_m, value), ascending
    weights: dict[str, float]
    services: dict[str, ServiceRule]
    min_place_confidence: float
    max_snap_distance_m: float
    dedupe_distance_m: float


@dataclass(frozen=True)
class CityConfig:
    slug: str
    name: str
    bbox: BBox
    edge_buffer_m: float
    analysis_epsg: int
    data_dir: Path
    overture_release: str
    state_fips: str | list[str]
    county_fips: str | list[str]
    acs_year: int
    gtfs_url: str | None
    scoring: Scoring

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / self.slug

    @property
    def interim_dir(self) -> Path:
        return self.data_dir / "interim" / self.slug

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed" / self.slug

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.interim_dir, self.processed_dir):
            d.mkdir(parents=True, exist_ok=True)

    def buffered_bbox(self, metres: float | None = None) -> BBox:
        m = self.edge_buffer_m if metres is None else metres
        return self.bbox.buffered(m, self.analysis_epsg)


@dataclass(frozen=True)
class Settings:
    """Secrets and connection settings from `.env` / the environment."""

    pghost: str
    pgport: int
    pgdatabase: str
    pguser: str | None
    pgpassword: str | None
    census_api_key: str | None


def settings() -> Settings:
    return Settings(
        pghost=os.environ.get("PGHOST", "localhost"),
        pgport=int(os.environ.get("PGPORT", "5436")),
        pgdatabase=os.environ.get("PGDATABASE", "urban_access"),
        pguser=os.environ.get("PGUSER"),
        pgpassword=os.environ.get("PGPASSWORD"),
        census_api_key=os.environ.get("CENSUS_API_KEY"),
    )


def default_city() -> str:
    return os.environ.get("CITY", "carson_city")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping")
    return data


def _tuple(v: Any) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    return tuple(str(x) for x in v)


def parse_scoring(raw: dict[str, Any], city_services: dict[str, Any] | None = None) -> Scoring:
    """Build Scoring from scoring.yaml contents, merging per-city service overrides."""
    try:
        bands = tuple((float(b["max_m"]), float(b["value"])) for b in raw["bands"])
        weights = {k: float(v) for k, v in raw["weights"].items()}
        services_raw = raw["services"]
    except (KeyError, TypeError) as e:
        raise ConfigError(f"scoring config missing key: {e}") from e

    if [b[0] for b in bands] != sorted(b[0] for b in bands):
        raise ConfigError("bands must be in ascending max_m order")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ConfigError(f"weights must sum to 1.0, got {sum(weights.values()):.4f}")
    if set(weights) != set(services_raw):
        raise ConfigError(f"weights {sorted(weights)} != services {sorted(services_raw)}")

    default_conf = float(raw.get("min_place_confidence", 0.5))
    city_services = city_services or {}
    unknown = set(city_services) - set(services_raw)
    if unknown:
        raise ConfigError(f"city overrides for unknown services: {sorted(unknown)}")

    services: dict[str, ServiceRule] = {}
    for name, s in services_raw.items():
        s = dict(s or {})
        over = dict(city_services.get(name) or {})
        services[name] = ServiceRule(
            name=name,
            taxonomy=_tuple(s.get("taxonomy")),
            min_confidence=float(over.get("min_confidence", s.get("min_confidence", default_conf))),
            include_names=_tuple(s.get("include_names")) + _tuple(over.get("include_names")),
            exclude_names=_tuple(s.get("exclude_names")) + _tuple(over.get("exclude_names")),
            landuse={k: _tuple(v) for k, v in (s.get("landuse") or {}).items()},
            secondary={k: _tuple(v) for k, v in (s.get("secondary") or {}).items()},
            prefer_gtfs=bool(s.get("prefer_gtfs", False)),
        )

    return Scoring(
        walk_speed_m_per_min=float(raw.get("walk_speed_m_per_min", 80)),
        bands=bands,
        weights=weights,
        services=services,
        min_place_confidence=default_conf,
        max_snap_distance_m=float(raw.get("max_snap_distance_m", 200)),
        dedupe_distance_m=float(raw.get("dedupe_distance_m", 75)),
    )


def parse_city(raw: dict[str, Any], scoring_raw: dict[str, Any]) -> CityConfig:
    try:
        slug = str(raw["slug"])
        b = raw["bbox"]
        bbox = BBox(
            float(b["lon_min"]), float(b["lat_min"]), float(b["lon_max"]), float(b["lat_max"])
        )
        release = str(raw["overture"]["release"])
        census = raw["census"]
    except (KeyError, TypeError) as e:
        raise ConfigError(f"city config missing key: {e}") from e
    if not slug.isidentifier() or slug != slug.lower():
        raise ConfigError(f"slug must be a lowercase identifier (it is a schema name): {slug!r}")

    crs = raw.get("analysis_crs", "auto")
    epsg = utm_epsg(*bbox.centroid) if crs in (None, "auto") else int(str(crs).split(":")[-1])

    data_dir = Path(os.path.expanduser(str(raw.get("data_dir", "data"))))
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir

    transit = raw.get("transit") or {}
    return CityConfig(
        slug=slug,
        name=str(raw.get("name", slug)),
        bbox=bbox,
        edge_buffer_m=float(raw.get("edge_buffer_m", 2000)),
        analysis_epsg=epsg,
        data_dir=data_dir,
        overture_release=release,
        state_fips=census.get("state_fips", "auto"),
        county_fips=census.get("county_fips", "auto"),
        acs_year=int(census["acs_year"]),
        gtfs_url=transit.get("gtfs_url"),
        scoring=parse_scoring(scoring_raw, raw.get("services")),
    )


def load_city(slug: str | None = None) -> CityConfig:
    """Load config/cities/<slug>.yaml merged with config/scoring.yaml."""
    slug = slug or default_city()
    city_raw = _read_yaml(CONFIG_DIR / "cities" / f"{slug}.yaml")
    if city_raw.get("slug") != slug:
        raise ConfigError(f"slug in file ({city_raw.get('slug')!r}) != file name ({slug!r})")
    return parse_city(city_raw, _read_yaml(CONFIG_DIR / "scoring.yaml"))
