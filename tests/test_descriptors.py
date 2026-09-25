from datetime import UTC, datetime

import pytest

from youtrained.descriptors import BANNED_PHRASES, list_descriptors, load_descriptor
from youtrained.loaders import LOADERS


def test_every_loader_has_a_descriptor():
    assert set(LOADERS) <= set(list_descriptors())


@pytest.mark.parametrize("name", list_descriptors())
def test_descriptor_validates_and_is_sourced(name):
    d = load_descriptor(name)
    assert d.litigation_context.last_reviewed <= datetime.now(UTC).date()
    assert d.litigation_context.sources, "litigation context needs at least one source"
    for action in d.what_you_can_do:
        assert action.sources, f"{name}: action {action.title!r} has no sources"
    assert d.usage.cited_by, f"{name}: list at least one model that uses it"
    assert set(d.usage.role) <= {"training", "evaluation"}


@pytest.mark.parametrize("name", list_descriptors())
def test_wording_rules(name):
    d = load_descriptor(name)
    for label, text in d.text_fields():
        lowered = text.lower()
        for phrase in BANNED_PHRASES:
            assert phrase not in lowered, f"{name}.{label} contains banned phrase {phrase!r}"
