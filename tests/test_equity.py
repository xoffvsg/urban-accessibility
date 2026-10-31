import numpy as np
import pandas as pd
import pytest

from urban_access.equity import (
    bg_shares,
    draw_acs,
    group_shares,
    origin_indicators,
    weighted_terciles,
)

BANDS = [400.0, 800.0, 1600.0]


def test_weighted_terciles_equal_weights():
    v = pd.Series([10, 20, 30, 40, 50, 60])
    t = weighted_terciles(v, pd.Series([1] * 6))
    assert t.tolist() == [1, 1, 2, 2, 3, 3]


def test_weighted_terciles_uneven_weights():
    # one block group holds half the residents: it alone spans the middle third
    v = pd.Series([10, 20, 30, 40])
    t = weighted_terciles(v, pd.Series([1, 1, 4, 2]))
    assert t.tolist() == [1, 1, 2, 3]


def test_weighted_terciles_ties_and_missing():
    v = pd.Series([10, 10, 30, np.nan, 50])
    t = weighted_terciles(v, pd.Series([1, 1, 1, 1, 0]))
    assert t.iloc[0] == t.iloc[1]
    assert pd.isna(t.iloc[3]) and pd.isna(t.iloc[4])


def _od():
    # origin 1: grocery 300 m, convenience 100 m; origin 2: grocery 900 m, convenience 200 m;
    # origin 3: no grocery within 1600 m (NaN)
    return pd.DataFrame(
        {
            "origin_id": [1, 1, 2, 2, 3, 3],
            "service": ["grocery", "convenience"] * 3,
            "dist_m": [300, 100, 900, 200, np.nan, 2000],
        }
    )


def test_origin_indicators():
    ind = origin_indicators(_od(), BANDS)
    assert ind.loc[1, "grocery_le_400m"] and not ind.loc[2, "grocery_le_400m"]
    assert ind.loc[2, "grocery_le_1600m"] and not ind.loc[3, "grocery_le_1600m"]
    assert ind["grocery_none_le_1600m"].tolist() == [False, False, True]
    assert ind["convenience_not_grocery_le_400m"].tolist() == [False, True, False]


def test_bg_shares_population_weighted():
    ind = origin_indicators(_od(), BANDS)
    origins = pd.DataFrame({"id": [1, 2, 3], "bg_geoid": ["a", "a", "b"], "pop": [3.0, 1.0, 5.0]})
    sh = bg_shares(ind, origins)
    assert sh.loc["a", "grocery_le_400m"] == pytest.approx(0.75)
    assert sh.loc["a", "grocery_le_1600m"] == pytest.approx(1.0)
    assert sh.loc["b", "grocery_le_1600m"] == pytest.approx(0.0)
    # cumulative bands never decrease
    assert (sh["grocery_le_800m"] >= sh["grocery_le_400m"]).all()


def test_group_shares_point_and_draws():
    shares = pd.DataFrame({"x": [1.0, 0.0]}, index=["a", "b"])
    assert group_shares(shares, pd.Series([1.0, 3.0]))[0] == pytest.approx(0.25)
    draws = np.array([[1.0, 3.0], [1.0, 1.0], [0.0, 2.0]])
    assert group_shares(shares, draws)[:, 0] == pytest.approx([0.25, 0.5, 0.0])
    # NaN share (no residents) is ignored, not propagated
    shares.loc["b", "x"] = np.nan
    assert group_shares(shares, pd.Series([1.0, 3.0]))[0] == pytest.approx(1.0)


def test_draw_acs_reproducible_clipped_centred():
    bg = pd.DataFrame({"n": [100.0, 5.0], "n_moe": [16.45, 50.0]})
    a = draw_acs(bg, ["n"], 4000, np.random.default_rng(1))["n"]
    b = draw_acs(bg, ["n"], 4000, np.random.default_rng(1))["n"]
    assert np.array_equal(a, b)
    assert a.shape == (4000, 2) and (a >= 0).all()
    assert a[:, 0].mean() == pytest.approx(100, abs=0.5)
    assert a[:, 0].std() == pytest.approx(10, rel=0.05)
