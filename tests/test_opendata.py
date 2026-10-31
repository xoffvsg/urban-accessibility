import math

from urban_access.opendata import nppes_rows, parse_geocoder


def test_parse_geocoder():
    text = (
        '"1","1001 MOUNTAIN ST, CARSON CITY, NV, 89703","Match","Non_Exact",'
        '"1001 N MOUNTAIN ST, CARSON CITY, NV, 89703","-119.7728,39.1696","199517012","L"\n'
        '"3","NOWHERE 99999, CARSON CITY, NV, 89706","No_Match"\n'
    )
    g = parse_geocoder(text)
    assert list(g["id"]) == ["1", "3"]
    assert list(g["geocoded"]) == [True, False]
    assert (g.at[0, "lon"], g.at[0, "lat"]) == (-119.7728, 39.1696)
    assert math.isnan(g.at[1, "lon"])


def test_nppes_rows_uses_location_and_primary_taxonomy():
    res = [
        {
            "number": 123,
            "basic": {"organization_name": "ACME CLINIC LLC", "status": "A"},
            "other_names": [{"organization_name": "ACME URGENT CARE"}],
            "taxonomies": [
                {"code": "207RC0000X", "desc": "Cardiology", "primary": False},
                {"code": "261QU0200X", "desc": "Clinic/Center, Urgent Care", "primary": True},
            ],
            "addresses": [
                {
                    "address_purpose": "MAILING",
                    "address_1": "PO BOX 1",
                    "city": "X",
                    "state": "NV",
                    "postal_code": "897010000",
                },
                {
                    "address_purpose": "LOCATION",
                    "address_1": "1 MAIN ST",
                    "city": "CARSON CITY",
                    "state": "NV",
                    "postal_code": "897031234",
                },
            ],
        },
        {"number": 9, "basic": {}, "taxonomies": [], "addresses": []},  # no location: skipped
    ]
    rows = nppes_rows(res)
    assert len(rows) == 1
    r = rows[0]
    assert (r["source_id"], r["taxonomy_code"], r["street"], r["zip"]) == (
        "123",
        "261QU0200X",
        "1 MAIN ST",
        "89703",
    )
    assert r["other_names"] == "ACME URGENT CARE"


import geopandas as gpd  # noqa: E402
import pytest  # noqa: E402
from shapely.geometry import box  # noqa: E402

from urban_access import opendata  # noqa: E402


@pytest.mark.parametrize(
    ("zone", "rule"),
    [
        ("SFR-8,000", "one_per_parcel"),
        ("sfr-1/2", "one_per_parcel"),
        ("RA-5", "one_per_parcel"),
        ("A-19", "one_per_parcel"),
        ("WASHOE TRIBE", "one_per_parcel"),
        ("MFR", "floor_area"),
        ("GC", "none"),
        ("AP", "none"),
        (None, "none"),
    ],
)
def test_zone_rule(zone, rule):
    one = ["SFR-*", "RA-*", "A-*", "FR-*", "WASHOE TRIBE"]
    assert opendata.zone_rule(zone, one, ["MFR"]) == rule


def test_assessor_parcels_keeps_only_requested_fields(monkeypatch):
    seen = {}

    def fake_query(url, *, out_fields="*", **kw):
        seen["out_fields"] = out_fields
        return gpd.GeoDataFrame(
            {
                "OBJECTID": [1, 2],
                "Total_DWUn": [2, None],
                "LU_Categor": ["Medium", None],
                "Legal_Owne": ["SOMEONE", "X"],
            },  # a server could send more than asked
            geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
            crs=4326,
        )

    monkeypatch.setattr(opendata.arcgis, "query", fake_query)
    h = {"county": "32510", "url": "u", "units_field": "Total_DWUn", "category_field": "LU_Categor"}
    g = opendata._assessor_parcels(h)
    assert seen["out_fields"] == "OBJECTID,Total_DWUn,LU_Categor"
    assert list(g.columns) == ["parcel_id", "county", "units", "rule", "category", "geometry"]
    assert list(g["units"]) == [2.0, 0.0]
    assert list(g["parcel_id"]) == ["32510:1", "32510:2"]
