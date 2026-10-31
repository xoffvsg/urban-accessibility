import pytest

from urban_access.nces import private_schools, pss_grade, public_grade, serves_k8


@pytest.mark.parametrize(
    ("code", "grade"),
    [
        ("PK", -1),
        ("KG", 0),
        ("01", 1),
        ("08", 8),
        ("12", 12),
        ("UG", None),
        ("M", None),
        ("N", None),
    ],
)
def test_public_grade(code, grade):
    assert public_grade(code) == grade


@pytest.mark.parametrize(
    ("code", "grade"),
    [
        ("1", None),
        ("2", -1),
        ("3", 0),
        ("4", 0),
        ("5", 1),
        ("6", 1),
        ("13", 8),
        ("17", 12),
        ("", None),
    ],
)
def test_pss_grade(code, grade):
    assert pss_grade(code) == grade


@pytest.mark.parametrize(
    ("lo", "hi", "k8"),
    [
        (-1, 5, True),  # PK-5 elementary
        (6, 8, True),  # middle
        (0, 6, True),  # K-6
        (1, 12, True),  # combined 1-12 serves K-8 grades
        (-1, -1, False),  # PK only
        (9, 12, False),  # high school
        (None, None, False),  # ungraded / not reported
    ],
)
def test_serves_k8(lo, hi, k8):
    assert serves_k8(lo, hi) is k8


def test_private_schools_year_columns():
    import pandas as pd

    from urban_access.config import BBox

    csv = pd.DataFrame(
        {
            "PPIN": ["A1", "A2"],
            "PINST": ["IN SCHOOL", "FAR SCHOOL"],
            "PADDRS": ["1 Main St", "2 Main St"],
            "PCITY": ["X", "Y"],
            "LATITUDE24": ["39.1", "40.5"],
            "LONGITUDE24": ["-119.75", "-119.75"],
            "LOGR2024": ["2", "3"],
            "HIGR2024": ["13", "17"],
        }
    )
    out = private_schools(BBox(-119.8, 39.0, -119.7, 39.2), "2324", csv)
    assert list(out["source_id"]) == ["A1"]
    assert (out["grade_lo"].iloc[0], out["grade_hi"].iloc[0]) == (-1, 8)
    assert out.crs.to_epsg() == 4326
