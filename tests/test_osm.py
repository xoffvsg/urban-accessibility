import pytest

from urban_access.osm import is_closed


@pytest.mark.parametrize(
    ("tags", "closed"),
    [
        ({"barrier": "gate"}, False),  # untagged: unknown, not modelled
        ({"barrier": "gate", "access": "private"}, False),  # residents pass
        ({"barrier": "gate", "access": "no"}, True),
        ({"barrier": "gate", "foot": "no"}, True),
        ({"barrier": "gate", "access": "permissive", "foot": "no"}, True),
        ({"barrier": "gate", "access": "no", "foot": "yes"}, False),
        ({"barrier": "gate", "access": "no", "foot": "designated"}, False),
    ],
)
def test_is_closed(tags, closed):
    assert is_closed(tags) is closed
