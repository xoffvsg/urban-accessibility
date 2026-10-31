import geopandas as gpd
import pytest
from shapely.geometry import Point

from urban_access.xcheck import (
    CONFIRMED,
    MOVED,
    OVERTURE_ONLY,
    SOURCE_ONLY,
    city_park_additions,
    health_sources,
    is_essential,
    match,
    name_similarity,
    suggest_flag,
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


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Smith's", "SMITHS FOOD & DRUG 356", True),
        ("Walmart", "Walmart 3408", True),
        ("Foodmaxx", "Food Maxx 449", True),
        ("Sierra Family Health Center", "HEALTH CO", False),  # one weak shared word
        ("Carson Tahoe Cardiology", "CARSON TAHOE REGIONAL HEALTHCARE", False),
    ],
)
def test_name_similarity_stores_and_clinics(a, b, same):
    threshold = 0.75 if "Carson" in a else 0.6
    assert (name_similarity(a, b) >= threshold) is same


@pytest.mark.parametrize(
    ("status", "in_service", "qualifies", "out"),
    [
        (CONFIRMED, True, True, "ok"),
        (CONFIRMED, False, True, "add"),
        (CONFIRMED, True, False, "drop"),
        (MOVED, False, True, "review"),  # name only, far away: never add on that
        (MOVED, True, False, "review"),
        (MOVED, False, False, "ok"),
        (OVERTURE_ONLY, True, False, "review"),
        (SOURCE_ONLY, False, True, "missing"),
    ],
)
def test_suggest_flag(status, in_service, qualifies, out):
    assert suggest_flag(status, in_service, qualifies) == out


def test_is_essential():
    prefixes = ["207Q", "207R00000X", "3336"]
    assert is_essential("207Q00000X", prefixes)  # family medicine
    assert is_essential("207R00000X", prefixes)  # internal medicine, general
    assert not is_essential("207RC0000X", prefixes)  # cardiovascular disease
    assert is_essential("3336C0003X", prefixes)  # community pharmacy
    assert not is_essential(None, prefixes)


def test_health_sources_explodes_names():
    nppes_orgs = gpd.GeoDataFrame(
        {
            "source_id": ["1"],
            "name": ["NEVADA CVS PHARMACY LLC"],
            "other_names": ["CVS PHARMACY #08779; CVS 8779"],
            "taxonomy_code": ["3336C0003X"],
            "taxonomy": ["Pharmacy"],
            "address": ["x"],
        },
        geometry=[Point(-119.76, 39.15)],
        crs=4326,
    )
    hrsa = gpd.GeoDataFrame(
        {
            "source_id": ["h1", "h2"],
            "name": ["Clinic", "Van"],
            "site_type": ["Permanent", "Mobile Van"],
            "setting": ["All Other Clinic Types", "All Other Clinic Types"],
            "status": ["Active", "Active"],
            "address": ["y", "y"],
        },
        geometry=[Point(-119.75, 39.18)] * 2,
        crs=4326,
    )
    s = health_sources(nppes_orgs, hrsa, ["3336"])
    assert sorted(s.loc[s["source"] == "nppes", "name"]) == [
        "CVS 8779",
        "CVS PHARMACY #08779",
        "NEVADA CVS PHARMACY LLC",
    ]
    assert s.loc[s["source"] == "nppes", "essential"].all()
    assert dict(
        zip(
            s.loc[s["source"] == "hrsa", "name"],
            s.loc[s["source"] == "hrsa", "essential"],
            strict=True,
        )
    ) == {
        "Clinic": True,
        "Van": False,
    }


def test_city_park_additions():
    from shapely.geometry import box

    city = gpd.GeoDataFrame(
        {
            "name": ["Covered Park", "New Park", "Gun Club", "Open Hills"],
            "category": ["PARK", "PARK", "SPORT", "OPEN SPACE"],
        },
        geometry=[
            box(0, 0, 100, 100),
            box(500, 0, 600, 100),
            box(1000, 0, 1100, 100),
            box(2000, 0, 2100, 100),
        ],
        crs=EPSG,
    )
    overture = gpd.GeoDataFrame({"name": ["x"]}, geometry=[box(50, 50, 150, 150)], crs=EPSG)
    add = city_park_additions(city, overture, ["PARK", "SPORT"], ["gun club"], EPSG)
    assert list(add) == [False, True, False, False]
