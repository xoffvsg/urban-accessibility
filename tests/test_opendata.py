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
