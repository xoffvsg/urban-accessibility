import pytest

from urban_access import arcgis


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _feature(i):
    return {
        "type": "Feature",
        "properties": {"id": i},
        "geometry": {"type": "Point", "coordinates": [i, 0]},
    }


def test_query_pages_until_done(monkeypatch):
    pages = [
        {
            "type": "FeatureCollection",
            "features": [_feature(1), _feature(2)],
            "properties": {"exceededTransferLimit": True},
        },
        {"type": "FeatureCollection", "features": [_feature(3)]},
    ]
    offsets = []

    def fake_get(url, params, timeout):
        offsets.append(params["resultOffset"])
        return _Resp(pages[len(offsets) - 1])

    monkeypatch.setattr(arcgis.requests, "get", fake_get)
    g = arcgis.query("https://example/FeatureServer/0")
    assert list(g["id"]) == [1, 2, 3]
    assert offsets == ["0", "2"]
    assert g.crs.to_epsg() == 4326


def test_query_raises_on_error(monkeypatch):
    monkeypatch.setattr(arcgis.requests, "get", lambda *a, **k: _Resp({"error": {"code": 400}}))
    with pytest.raises(RuntimeError):
        arcgis.query("https://example/FeatureServer/0", geometry=False)
