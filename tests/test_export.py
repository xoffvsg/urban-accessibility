import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from urban_access.config import load_city
from urban_access.export import block_group_features, service_features, summary

CFG = load_city("carson_city")
SVC = CFG.scoring.scored_services


def _bg():
    n = 2
    d = {
        "geoid": ["325100001001", "325100006013"],
        "statefp": ["32"] * n,
        "countyfp": ["510"] * n,
        "tractce": ["000100", "000601"],
        "blkgrpce": ["1", "3"],
        "pop_total": [1200.0, 0.0],
        "pop_total_moe": [200.0, 11.0],
        "access_score": [41.234, np.nan],
        "access_rank": [3, pd.NA],
        "access_pctile": [90.0, np.nan],
        "grocery_n_400m": [1, 0],
        "people_per_grocery": [600.0, np.nan],
        "median_hh_income": [60000.0, np.nan],
        "median_hh_income_moe": [30000.0, np.nan],
        "median_hh_income_cv": [30.4, np.nan],
        "zero_car_hh_pct": [5.0, np.nan],
        "zero_car_hh_pct_cv": [80.0, np.nan],
        "below_pov_pct": [20.0, np.nan],
        "below_pov_pct_cv": [10.0, np.nan],
        "pop_65plus_pct": [15.0, np.nan],
        "pop_65plus": [180.0, 0.0],
        "pop_65plus_moe": [40.0, 11.0],
        "heavy_column": ["x", "y"],
    }
    for s in SVC:
        d |= {f"{s}_score": [50.0, np.nan], f"{s}_pop_5min_pct": [10.0, np.nan],
              f"{s}_dist_centroid_m": [500.0, np.nan]}  # fmt: skip
    return gpd.GeoDataFrame(d, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=4326)


def _eq():
    return pd.DataFrame(
        {
            "geoid": ["325100001001", "325100006013"],
            "lisa_cluster": ["HH", None],
            "income_tercile": [2, pd.NA],
        }  # fmt: skip
    )


def _feats():
    return block_group_features(_bg(), _eq(), CFG.scoring, {"32510": "Carson City"})


def test_block_group_properties_and_crs():
    f = _feats()
    assert f.crs.to_epsg() == 4326
    assert "heavy_column" not in f and "pop_65plus" not in f
    assert {f"{s}_score" for s in SVC} <= set(f.columns)
    assert f.loc[0, "label"] == "Tract 1, BG 1 (Carson City)"
    assert f.loc[1, "label"] == "Tract 6.01, BG 3 (Carson City)"
    assert f.loc[0, "access_score"] == pytest.approx(41.2)
    assert not f.loc[1, "has_residents"] and f.loc[1, "lisa_cluster"] == ""


def test_uncertainty_flags():
    f = _feats()
    assert f.loc[0, "median_hh_income_uncertain"]  # CV 30.4 > 30
    assert f.loc[0, "zero_car_hh_pct_uncertain"]
    assert not f.loc[0, "below_pov_pct_uncertain"]
    # 65+: CV derived from the count, 100 * (40 / 1.645) / 180 = 13.5 %
    assert not f.loc[0, "pop_65plus_pct_uncertain"]
    assert not f.loc[1, "median_hh_income_uncertain"]  # NaN → not flagged


def test_service_features_drop_park_access_points():
    d = gpd.GeoDataFrame(
        {
            "service": ["grocery", "parks", "parks"],
            "name": ["A", None, "P"],
            "secondary": [False] * 3,
            "source": ["overture_places", "landuse_access", "overture_places"],
        },
        geometry=[Point(0, 0)] * 3,
        crs=4326,
    )
    s = service_features(d)
    assert len(s) == 2 and set(s.columns) == {"service", "name", "secondary", "geometry"}


def test_summary_keys_and_weights():
    s = summary(_feats(), CFG, {"headline": [{"metric": "grocery_le_400m", "pct": 2.123}]})
    for k in ("city", "bounds", "overture_release", "acs_year", "weights", "web", "block_groups",
              "attribution", "headline"):  # fmt: skip
        assert k in s
    assert sum(s["weights"].values()) == pytest.approx(1)
    assert s["web"]["class_breaks"][0] == 0 and s["web"]["class_breaks"][-1] == 100
    assert len(s["web"]["class_colors"]) == len(s["web"]["class_breaks"]) - 1
    assert s["block_groups"][1]["access_score"] is None
    assert s["block_groups"][0]["bbox"] == [0, 0, 1, 1]
    assert s["headline"][0]["pct"] == 2.1
