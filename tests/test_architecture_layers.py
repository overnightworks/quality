"""A layers contract's head is compared with the modules the base does not name stripped."""

import pytest

from tests.support.architecture import GUARDED_CHANGES, contracts_toml, loosened, one


def exhaustive(*layers: str, **options) -> dict:
    return {"type": "layers", "layers": list(layers), "containers": ["shop"], "exhaustive": True, **options}


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (exhaustive("a", "b"), exhaustive("a", "new", "b"), []),
        (exhaustive("a | b"), exhaustive("a | b | new"), []),
        (exhaustive("a", "b"), exhaustive("new", "a", "b"), []),
        (exhaustive("a", "b"), exhaustive("a", "b", "new"), []),
        (exhaustive("a", "b"), exhaustive("a", "new | other", "b"), []),
        (exhaustive("a : b"), exhaustive("a : new : b"), []),
        (exhaustive("a", "b"), exhaustive("b", "a"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("a", "b"), exhaustive("a | b"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("a", "b"), exhaustive("a"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("a", "b | c"), exhaustive("a | c", "b"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("a | b"), exhaustive("a : b"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("a", "b"), exhaustive("a", "(b)"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("a", "b"), exhaustive("a", "b", containers=["shop", "other"]), [GUARDED_CHANGES + "containers"]),
        (exhaustive("a", "b"), exhaustive("a", "b", exhaustive=False), [GUARDED_CHANGES + "exhaustive"]),
        (exhaustive("a", "b"), exhaustive("a", "b", exhaustive_ignores=["new"]), [GUARDED_CHANGES + "exhaustive_ignores"]),
        (exhaustive("a", "b"), exhaustive("a", "new", "b", exhaustive=False), [GUARDED_CHANGES + "exhaustive"]),
        (exhaustive("a", "b"), exhaustive("b", "new", "a"), [GUARDED_CHANGES + "layers"]),
    ],
    ids=[
        "a module inserted between layers", "a module added to an independent layer", "a module added on top",
        "a module added at the bottom", "an added layer of two new modules", "a module added to a dependent layer",
        "a reorder", "a merge", "a removal", "a module moved to another layer", "independent turned dependent",
        "a required module made optional", "changed containers", "exhaustive switched off", "a new exhaustive ignore",
        "an insert next to exhaustive switched off", "a reorder next to an insert",
    ],
)
def test_a_layers_contract_is_compared_without_the_modules_the_base_does_not_name(base, head, expected):
    """LOOSE-29, LOOSE-31, LOOSE-33, LOOSE-34: The head's layers lose every module the base does not name, then compare
    as before."""
    assert loosened({"pyproject.toml": contracts_toml(one(base))}, {"pyproject.toml": contracts_toml(one(head))}) == expected


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (exhaustive("a", "b"), exhaustive("a : new", "b"), []),
        (exhaustive("(a)", "b"), exhaustive("(a) : (new)", "b"), []),
        (exhaustive("a", "b"), exhaustive("a", "b : new"), []),
        (exhaustive("a | b"), exhaustive("a : b : new"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("a", "b"), exhaustive("a : b : new"), [GUARDED_CHANGES + "layers"]),
        (exhaustive("(a)", "b"), exhaustive("a : new", "b"), [GUARDED_CHANGES + "layers"]),
    ],
    ids=[
        "a new module beside a known one in a layer of its own",
        "a new optional module beside a known optional one",
        "a new module beside the known bottom layer",
        "two known independent modules turned dependent beside a new one",
        "two known layers merged into one dependent layer beside a new one",
        "a known optional module made required beside a new one",
    ],
)
def test_a_new_sibling_loosens_nothing_unless_the_known_part_of_its_layer_changes(base, head, expected):
    """LOOSE-30: A layer left with one known module has no sibling, so its independence says nothing."""
    assert loosened({"pyproject.toml": contracts_toml(one(base))}, {"pyproject.toml": contracts_toml(one(head))}) == expected
