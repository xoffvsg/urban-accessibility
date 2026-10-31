import geopandas as gpd
import pytest
from shapely.geometry import Point

from urban_access.config import ServiceRule, parse_scoring
from urban_access.services import classify_places, normalise_name

# UTM 11N metres → WGS84 is handled by geopandas; build points in 32611 and convert
EPSG = 32611


def _scoring(**overrides):
    raw = {
        "bands": [{"max_m": 400, "value": 1.0}],
        "weights": {"grocery": 0.5, "schools": 0.5},
        "services": {
            "grocery": {
                "taxonomy": ["grocery_store"],
                "secondary": {"convenience": ["convenience_store"]},
            },
            "schools": {"taxonomy": ["elementary_school"], "min_confidence": 0.6},
        },
        "min_place_confidence": 0.5,
        "dedupe_distance_m": 75,
    }
    return parse_scoring(raw, overrides)


def _places(rows):
    """rows: (name, primary, hierarchy, conf, status, x_m, y_m)"""
    df = gpd.GeoDataFrame(
        {
            "id": [f"p{i}" for i in range(len(rows))],
            "name": [r[0] for r in rows],
            "taxonomy_primary": [r[1] for r in rows],
            "taxonomy_hierarchy": [r[2] for r in rows],
            "taxonomy_alternates": [None] * len(rows),
            "confidence": [r[3] for r in rows],
            "operating_status": [r[4] for r in rows],
        },
        geometry=[Point(260000 + r[5], 4340000 + r[6]) for r in rows],
        crs=EPSG,
    )
    return df.to_crs(4326)


SHOP = ["shopping", "food_and_beverage_store", "grocery_store"]


def _status(out, name, service):
    sel = out[(out["name"] == name) & (out["service"] == service)]
    return sel["status"].tolist()


def test_hierarchy_match_and_secondary():
    pl = _places(
        [
            ("Smith's", "grocery_store", SHOP, 0.99, "open", 0, 0),
            ("7-Eleven", "convenience_store", ["shopping", "convenience_store"], 0.9, None, 500, 0),
            ("Bike Shop", "bike_store", ["shopping", "bike_store"], 0.9, "open", 900, 0),
        ]
    )
    out = classify_places(pl, _scoring(), EPSG)
    assert _status(out, "Smith's", "grocery") == ["assigned"]
    conv = out[out["service"] == "convenience"]
    assert conv["name"].tolist() == ["7-Eleven"] and conv["secondary"].all()
    assert "Bike Shop" not in out["name"].tolist()


def test_ancestor_in_hierarchy_matches():
    s = _scoring()
    s.services["grocery"] = ServiceRule("grocery", taxonomy=("food_and_beverage_store",))
    pl = _places(
        [("Butcher", "butcher_shop", ["shopping", "food_and_beverage_store"], 0.9, "open", 0, 0)]
    )
    assert _status(classify_places(pl, s, EPSG), "Butcher", "grocery") == ["assigned"]


def test_include_and_exclude_names():
    s = _scoring(grocery={"include_names": ["^raley'?s"], "exclude_names": ["popcorn"]})
    pl = _places(
        [
            ("Raley's", "coffee_shop", ["eat_and_drink", "coffee_shop"], 0.99, "open", 0, 0),
            ("Kernels Popcorn", "grocery_store", SHOP, 0.9, "open", 500, 0),
        ]
    )
    out = classify_places(pl, s, EPSG)
    assert _status(out, "Raley's", "grocery") == ["assigned"]
    assert _status(out, "Kernels Popcorn", "grocery") == ["excluded_name"]


def test_closed_and_low_confidence():
    pl = _places(
        [
            ("Arco", "grocery_store", SHOP, 0.95, "permanently_closed", 0, 0),
            ("Ghost", "grocery_store", SHOP, 0.3, "open", 500, 0),
            (
                "School A",
                "elementary_school",
                ["education", "elementary_school"],
                0.55,
                None,
                900,
                0,
            ),
        ]
    )
    out = classify_places(pl, _scoring(), EPSG)
    assert _status(out, "Arco", "grocery") == ["closed"]
    assert _status(out, "Ghost", "grocery") == ["low_confidence"]
    # per-service min_confidence (0.6) applies to schools
    assert _status(out, "School A", "schools") == ["low_confidence"]


def test_dedupe_same_name_nearby_only():
    pl = _places(
        [
            ("Save Mart", "grocery_store", SHOP, 0.77, None, 0, 0),
            ("Save-Mart", "grocery_store", SHOP, 0.99, "open", 40, 0),  # 40 m: duplicate
            ("Save Mart", "grocery_store", SHOP, 0.9, "open", 3000, 0),  # 3 km: separate store
        ]
    )
    out = classify_places(pl, _scoring(), EPSG)
    g = out[out["service"] == "grocery"].sort_values("confidence", ascending=False)
    assert g["status"].tolist() == ["assigned", "assigned", "duplicate"]
    assert g.iloc[2]["confidence"] == pytest.approx(0.77)  # lower confidence copy dropped


def test_normalise_name():
    assert normalise_name("Trader Joe's") == normalise_name("TRADER JOES")
    assert normalise_name(None) == ""


def test_place_overrides_drop_and_move():
    pl = _places(
        [
            ("Walmart", "grocery_store", SHOP, 0.99, "open", 0, 0),
            ("Walmart", "grocery_store", SHOP, 0.99, "open", 3000, 0),
            ("Sprouts", "grocery_store", SHOP, 0.99, "open", 6000, 0),
        ]
    )
    pl["id"] = ["aaaaaaaa-1", "bbbbbbbb-2", "cccccccc-3"]
    target = gpd.GeoSeries([Point(260100, 4340100)], crs=EPSG).to_crs(4326).iloc[0]
    raw = {
        "bands": [{"max_m": 400, "value": 1.0}],
        "weights": {"grocery": 1.0},
        "services": {"grocery": {"taxonomy": ["grocery_store"]}},
    }
    s = parse_scoring(
        raw,
        None,
        {
            "bbbbbbbb": {"drop": True, "note": "closed"},
            "cccccccc-3": {"lon": target.x, "lat": target.y},
        },
    )
    out = classify_places(pl, s, EPSG).set_index("id")
    assert out.loc["aaaaaaaa-1", "status"] == "assigned"
    assert out.loc["bbbbbbbb-2", "status"] == "removed"
    assert out.loc["bbbbbbbb-2", "override"] == "closed"
    assert out.loc["cccccccc-3", "ref"] == "cccccccc"
    moved = gpd.GeoSeries([out.loc["cccccccc-3", "geometry"]], crs=4326).to_crs(EPSG).iloc[0]
    assert moved.distance(Point(260100, 4340100)) < 0.01
    # the moved Sprouts is now 141 m from the first Walmart, but has a different name: no dedupe
    assert out.loc["cccccccc-3", "status"] == "assigned"


@pytest.mark.parametrize(
    "bad",
    [
        {"short": {"drop": True}},
        {"aaaaaaaa": {"lon": 1.0}},
        {"aaaaaaaa": {"note": "nothing to do"}},
        {"aaaaaaaa": {"drop": True, "colour": "red"}},
    ],
)
def test_place_overrides_validation(bad):
    from urban_access.config import ConfigError, parse_place_overrides

    with pytest.raises(ConfigError):
        parse_place_overrides(bad)
