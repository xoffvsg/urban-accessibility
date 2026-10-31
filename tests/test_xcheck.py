import geopandas as gpd
import pytest
from shapely.geometry import Point

from urban_access.xcheck import (
    CONFIRMED,
    MOVED,
    OVERTURE_ONLY,
    SOURCE_ONLY,
    match,
    name_similarity,
    suggest_school,
)

EPSG = 32611  # metres; build points in UTM directly


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Bordewich Bray Elementary School", "Bordewich/Bray Elementary", True),
        ("Fritsch Elementary School", "Edith W. Fritsch Elementary", True),
        ("St. Teresa's School", "ST TERESA OF AVAILA CATHOLIC SCHOOL", True),
        ("Piñon Hills Elementary School", "Pinon Hills Elementary School", True),
        ("I-School", "ISCHOOL", True),
        ("Carson Middle School", "Carson High School", False),
        ("Capital Christian School", "LEGACY CHRISTIAN ACADEMY", False),
    ],
)
def test_name_similarity(a, b, same):
    assert (name_similarity(a, b) >= 0.6) is same


def _gdf(rows):
    return gpd.GeoDataFrame(
        {"name": [r[0] for r in rows]}, geometry=[Point(r[1], r[2]) for r in rows], crs=EPSG
    )


def test_match_statuses():
    o = _gdf([("Alpha School", 0, 0), ("Beta School", 1000, 0), ("Gamma School", 5000, 0)])
    s = _gdf([("ALPHA SCHOOL", 50, 0), ("BETA SCHOOL", 1300, 0), ("DELTA SCHOOL", 9000, 0)])
    m = match(o, s, EPSG, confirm_m=150, max_m=500, min_sim=0.6)
    paired = m.dropna(subset=["o_idx"])
    by_o = dict(zip(o.loc[paired["o_idx"], "name"], paired["status"], strict=True))
    assert by_o["Alpha School"] == CONFIRMED
    assert by_o["Beta School"] == MOVED
    assert by_o["Gamma School"] == OVERTURE_ONLY
    assert (m["status"] == SOURCE_ONLY).sum() == 1


def test_match_is_one_to_one():
    o = _gdf([("Alpha School", 0, 0), ("Alpha School", 20, 0)])
    s = _gdf([("ALPHA SCHOOL", 10, 0)])
    m = match(o, s, EPSG, confirm_m=150, max_m=500, min_sim=0.6)
    assert (m["status"] == CONFIRMED).sum() == 1
    assert (m["status"] == OVERTURE_ONLY).sum() == 1


@pytest.mark.parametrize(
    ("status", "in_service", "serves", "open_", "out"),
    [
        (CONFIRMED, True, True, True, "ok"),
        (CONFIRMED, False, True, True, "add"),
        (CONFIRMED, True, False, True, "drop"),  # e.g. 9-12
        (CONFIRMED, True, True, False, "drop"),  # inactive
        (CONFIRMED, False, False, True, "ok"),  # high school, not counted
        (MOVED, True, True, True, "review"),
        (OVERTURE_ONLY, True, None, False, "review"),
        (OVERTURE_ONLY, False, None, False, "ok"),
        (SOURCE_ONLY, False, True, True, "missing"),
        (SOURCE_ONLY, False, None, False, "ok"),
    ],
)
def test_suggest_school(status, in_service, serves, open_, out):
    assert suggest_school(status, in_service, serves, open_) == out
