import copy

import pytest
import yaml

from urban_access.config import (
    CONFIG_DIR,
    BBox,
    ConfigError,
    load_city,
    parse_city,
    parse_scoring,
    utm_epsg,
)


@pytest.fixture
def scoring_raw():
    return yaml.safe_load((CONFIG_DIR / "scoring.yaml").read_text())


@pytest.fixture
def city_raw():
    return yaml.safe_load((CONFIG_DIR / "cities" / "carson_city.yaml").read_text())


def test_carson_city_loads():
    cfg = load_city("carson_city")
    assert cfg.slug == "carson_city"
    assert cfg.analysis_epsg == 32611
    assert abs(sum(cfg.scoring.weights.values()) - 1) < 1e-9
    assert cfg.raw_dir.parts[-2:] == ("raw", "carson_city")


@pytest.mark.parametrize(
    "lon,lat,epsg",
    [(-119.75, 39.1, 32611), (-122.4, 37.8, 32610), (2.35, 48.85, 32631), (151.2, -33.9, 32756)],
)
def test_utm_epsg(lon, lat, epsg):
    assert utm_epsg(lon, lat) == epsg


def test_buffered_bbox_covers_at_least_the_buffer():
    b = BBox(-119.8, 39.0, -119.7, 39.2)
    bb = b.buffered(2000, 32611)
    # 2 km ≈ 0.018° latitude, ≈ 0.023° longitude at 39° N. The envelope of the rotated UTM
    # box is larger (conservative for fetching) but should stay well under 2x the buffer.
    for d in (b.lat_min - bb.lat_min, bb.lat_max - b.lat_max):
        assert 0.018 <= d < 0.036
    for d in (b.lon_min - bb.lon_min, bb.lon_max - b.lon_max):
        assert 0.023 <= d < 0.046


def test_bbox_rejects_inverted():
    with pytest.raises(ConfigError):
        BBox(-119.7, 39.0, -119.8, 39.2)


def test_weights_must_sum_to_one(scoring_raw):
    bad = copy.deepcopy(scoring_raw)
    bad["weights"]["grocery"] += 0.1
    with pytest.raises(ConfigError, match="sum to 1"):
        parse_scoring(bad)


def test_weights_and_services_must_match(scoring_raw):
    bad = copy.deepcopy(scoring_raw)
    del bad["services"]["parks"]
    with pytest.raises(ConfigError, match="services"):
        parse_scoring(bad)


def test_bands_ascending(scoring_raw):
    bad = copy.deepcopy(scoring_raw)
    bad["bands"] = list(reversed(bad["bands"]))
    with pytest.raises(ConfigError, match="ascending"):
        parse_scoring(bad)


def test_city_overrides_merge(scoring_raw):
    s = parse_scoring(scoring_raw, {"grocery": {"include_names": ["x"], "min_confidence": 0.9}})
    assert "x" in s.services["grocery"].include_names
    assert s.services["grocery"].min_confidence == 0.9


def test_unknown_service_override_rejected(scoring_raw):
    with pytest.raises(ConfigError, match="unknown services"):
        parse_scoring(scoring_raw, {"bakeries": {}})


def test_missing_key(city_raw, scoring_raw):
    bad = copy.deepcopy(city_raw)
    del bad["bbox"]
    with pytest.raises(ConfigError, match="missing key"):
        parse_city(bad, scoring_raw)


def test_explicit_crs(city_raw, scoring_raw):
    raw = copy.deepcopy(city_raw)
    raw["analysis_crs"] = "EPSG:26911"
    assert parse_city(raw, scoring_raw).analysis_epsg == 26911


def test_blocked_segments(city_raw, scoring_raw):
    raw = copy.deepcopy(city_raw)
    raw.pop("blocked_segments", None)
    assert parse_city(raw, scoring_raw).blocked_segments == {}
    raw["blocked_segments"] = {"abc-123": "fence", "def-456": None}
    assert parse_city(raw, scoring_raw).blocked_segments == {"abc-123": "fence", "def-456": ""}


def test_gated_areas_path(city_raw, scoring_raw):
    cfg = parse_city(city_raw, scoring_raw)
    assert cfg.gated_areas_path is not None and cfg.gated_areas_path.exists()
    raw = copy.deepcopy(city_raw)
    raw.pop("gated_areas")
    assert parse_city(raw, scoring_raw).gated_areas_path is None
