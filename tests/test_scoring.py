import numpy as np
import pandas as pd
import pytest

from urban_access.config import load_city
from urban_access.scoring import band_value, edge_point_distance, score_block_groups

BANDS = ((400.0, 1.0), (800.0, 0.6), (1600.0, 0.3))


@pytest.mark.parametrize(
    "d,v",
    [
        (0, 1.0),
        (400, 1.0),
        (400.01, 0.6),
        (800, 0.6),
        (800.01, 0.3),
        (1600, 0.3),
        (1600.01, 0.0),
        (5000, 0.0),
        (np.nan, 0.0),
    ],
)
def test_band_edges(d, v):
    assert band_value([d], BANDS)[0] == v


def test_bands_match_config():
    assert load_city("carson_city").scoring.bands == BANDS


def test_edge_point_distance():
    # 100 m edge, point 30 % along, 5 m off; source 200 m, target 50 m from the service
    assert edge_point_distance(5, 0.3, 100, 200, 50) == pytest.approx(5 + 50 + 70)
    assert edge_point_distance(0, 0.0, 100, 10, 500) == pytest.approx(10)


def _fixture():
    scoring = load_city("carson_city").scoring
    services = scoring.scored_services
    origins = pd.DataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "kind": ["building", "building", "building", "centroid", "centroid"],
            "bg_geoid": ["A", "A", "B", "A", "B"],
            "pop": [300.0, 100.0, 0.0, 0.0, 0.0],
            "snap_m": [10, 250, 5, 0, 0],
        }
    )
    # every service: origin 1 at 300 m (1.0), origin 2 at 1000 m (0.3), origin 3 unreachable
    rows = []
    for s in services:
        rows += [
            (1, s, 300.0, 200.0),
            (2, s, 1000.0, 500.0),
            (3, s, np.nan, 900.0),
            (4, s, 650.0, 400.0),
            (5, s, np.nan, 1000.0),
        ]
    od = pd.DataFrame(rows, columns=["origin_id", "service", "dist_m", "euclid_m"])
    bg = pd.DataFrame({"geoid": ["A", "B"], "pop_total": [400.0, 0.0]})
    counts = pd.DataFrame(
        [("A", s, 2, 3) for s in services],
        columns=["bg_geoid", "service", "n_near", "n_people_radius"],
    )
    cover = pd.DataFrame(
        [("A", s, 42.0) for s in services], columns=["bg_geoid", "service", "cover_pct"]
    )
    return score_block_groups(od, origins, bg, counts, cover, scoring), scoring


def test_population_weighted_score_and_composite():
    out, scoring = _fixture()
    a = out.loc["A"]
    expected = 100 * (300 * 1.0 + 100 * 0.3) / 400  # 82.5
    for s in scoring.scored_services:
        assert a[f"{s}_score"] == pytest.approx(expected)
        assert a[f"{s}_pop_5min_pct"] == pytest.approx(75.0)
        assert a[f"{s}_n_400m"] == 2
        assert a[f"{s}_cover_400m_pct"] == pytest.approx(42.0)
        assert a[f"{s}_dist_centroid_m"] == pytest.approx(650.0)
    assert a["access_score"] == pytest.approx(expected)  # weights sum to 1
    assert a["people_per_grocery"] == pytest.approx(400 / 3)
    assert a["snap_flag_pop_pct"] == pytest.approx(25.0)
    assert a["circuity_ratio"] == pytest.approx((300 * 1.5 + 100 * 2.0) / 400)


def test_zero_population_block_group_is_nan_not_error():
    out, _ = _fixture()
    b = out.loc["B"]
    assert pd.isna(b["access_score"]) and pd.isna(b["grocery_score"])
    assert pd.isna(b["access_rank"])
    assert b["grocery_n_400m"] == 0 and pd.isna(b["people_per_grocery"])
