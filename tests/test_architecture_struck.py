import pytest

from quality import architecture
from quality.architecture import PullTree
from quality.ratchet import Renames
from tests.support.architecture import GUARDED_CHANGES, contracts_ini, loosened, toml

PYPROJECT = "pyproject.toml"
SHOP_FILES = ("__init__.py", "web.py", "api.py", "db.py")
BASE_PATHS = frozenset({f"src/shop/{name}" for name in (*SHOP_FILES, "dead.py")})
HEAD_PATHS = frozenset({f"src/shop/{name}" for name in SHOP_FILES})
FORBIDDEN_DEAD = {"type": "forbidden", "source_modules": ["shop.db", "shop.dead"], "forbidden_modules": ["shop.web"]}
LAYERS = {"type": "layers", "containers": ["shop"], "layers": ["web : dead"]}


def tree_of(base_paths=BASE_PATHS, head_paths=HEAD_PATHS, moves=None, empty=()):
    moves = moves or {}
    return PullTree(frozenset(base_paths), frozenset(head_paths), frozenset(empty), Renames(moves, frozenset(moves.values())))


def lines(base, head, tree="deleted", roots=("shop",)):
    """The loosenings of one contract named `guarded`; `tree` is the tree of a dead module's deletion by default."""
    tree = tree_of() if tree == "deleted" else tree
    return loosened({PYPROJECT: toml(base, roots)}, {PYPROJECT: toml(head, roots)}, tree)


def changes(option):
    return [f"{GUARDED_CHANGES}{option}"]


FIELD_CELLS = {
    "source_modules": (FORBIDDEN_DEAD, {**FORBIDDEN_DEAD, "source_modules": ["shop.db"]}),
    "forbidden_modules": (
        {**FORBIDDEN_DEAD, "source_modules": ["shop.db"], "forbidden_modules": ["shop.web", "shop.dead"]},
        {**FORBIDDEN_DEAD, "source_modules": ["shop.db"]},
    ),
    "modules": (
        {"type": "independence", "modules": ["shop.web", "shop.api", "shop.dead"]},
        {"type": "independence", "modules": ["shop.web", "shop.api"]},
    ),
    "protected_modules": (
        {"type": "protected", "protected_modules": ["shop.db", "shop.dead"], "allowed_importers": ["shop.api"]},
        {"type": "protected", "protected_modules": ["shop.db"], "allowed_importers": ["shop.api"]},
    ),
    "containers": (
        {"type": "layers", "containers": ["shop", "shop.dead"], "layers": ["web : api"]},
        {"type": "layers", "containers": ["shop"], "layers": ["web : api"]},
    ),
    "layers": (LAYERS, {**LAYERS, "layers": ["web"]}),
}


@pytest.mark.parametrize("option", FIELD_CELLS)
def test_a_module_struck_from_any_of_the_six_lists_is_no_loosening_when_the_head_no_longer_has_it(option):
    """LOOSE-19: a module struck from a list that the head's files no longer hold is no loosening."""
    base, head = FIELD_CELLS[option]
    assert lines(base, head) == []


@pytest.mark.parametrize("option", FIELD_CELLS)
def test_a_module_struck_from_any_of_the_six_lists_while_it_exists_stays_a_loosening(option):
    """LOOSE-20: a module the head's files still hold stays `changes <list>`."""
    base, head = FIELD_CELLS[option]
    assert lines(base, head, tree_of(head_paths=BASE_PATHS)) == changes(option)


@pytest.mark.parametrize("option", FIELD_CELLS)
def test_a_tree_that_was_not_read_counts_nothing_as_missing(option):
    """LOOSE-20: without a tree every strike stays `changes <list>`."""
    base, head = FIELD_CELLS[option]
    assert lines(base, head, None) == changes(option)


TOP = frozenset({"shop/__init__.py", "shop/web.py", "shop/dead.py"})


def test_a_root_package_at_the_top_of_the_repository_places_its_modules_without_a_directory():
    """LOOSE-19, LOOSE-20: a package at the repository top is placed at "", present while its file is tracked."""
    base, head = FIELD_CELLS["source_modules"]
    assert lines(base, head, tree_of(TOP, TOP - {"shop/dead.py"})) == []
    assert lines(base, head, tree_of(TOP, TOP)) == changes("source_modules")


API = {"type": "forbidden", "source_modules": ["shop.db", "shop.api"], "forbidden_modules": ["shop.web"]}
API_STRUCK = {**API, "source_modules": ["shop.db"]}
PACKAGE = {"src/shop/__init__.py", "src/shop/db.py", "src/shop/web.py", "src/shop/api/__init__.py"}


@pytest.mark.parametrize(
    ("head_paths", "expected"),
    [
        (PACKAGE - {"src/shop/api/__init__.py"}, []),
        ((PACKAGE - {"src/shop/api/__init__.py"}) | {"src/shop/api/x.py"}, changes("source_modules")),
        ((PACKAGE - {"src/shop/api/__init__.py"}) | {"src/shop/api/data.txt"}, changes("source_modules")),
        (PACKAGE | {"src/shop/api/x.py"}, changes("source_modules")),
    ],
    ids=["a package gone", "a namespace folder still holding a module", "a folder holding only data", "a package kept"],
)
def test_a_package_is_present_while_any_file_sits_under_its_folder(head_paths, expected):
    """LOOSE-19, LOOSE-20: no `<path>/__init__.py` and no file under `<path>/` is a deleted package."""
    assert lines(API, API_STRUCK, tree_of(PACKAGE, head_paths)) == expected


def source_modules_of(names):
    return {**FORBIDDEN_DEAD, "source_modules": names}


OTHER_STRUCK = {**FORBIDDEN_DEAD, "source_modules": ["shop.db", "other.x"]}


@pytest.mark.parametrize(
    ("base", "head", "roots", "head_paths", "base_paths", "option"),
    [
        (source_modules_of(["shop.db", "shop.*"]), source_modules_of(["shop.db"]), ("shop",), HEAD_PATHS, BASE_PATHS,
         "source_modules"),
        ({**FORBIDDEN_DEAD, "forbidden_modules": ["shop.web", "requests"]}, FORBIDDEN_DEAD | {"source_modules": ["shop.db", "shop.dead"]},
         ("shop",), BASE_PATHS, BASE_PATHS, "forbidden_modules"),
        (FORBIDDEN_DEAD, source_modules_of(["shop.db"]), ("shop",), HEAD_PATHS - {"src/shop/__init__.py"}, BASE_PATHS,
         "source_modules"),
        (FORBIDDEN_DEAD, source_modules_of(["shop.db"]), ("shop",), HEAD_PATHS | {"lib/shop/__init__.py"}, BASE_PATHS,
         "source_modules"),
        (FORBIDDEN_DEAD, source_modules_of(["shop.db"]), ("shop",), HEAD_PATHS, BASE_PATHS - {"src/shop/__init__.py"},
         "source_modules"),
        (OTHER_STRUCK, source_modules_of(["shop.db"]), ("shop", "other"), HEAD_PATHS, BASE_PATHS, "source_modules"),
    ],
    ids=["a wildcard", "a name outside the root packages", "a root package the head cannot place",
         "a root package with two production directories", "a root package only the head can place",
         "a second root package with no files"],
)
def test_a_strike_the_files_cannot_prove_dead_stays_a_loosening(base, head, roots, head_paths, base_paths, option):
    """LOOSE-20: a wildcard, a name outside the root packages and a root package the files cannot place stay red."""
    assert lines(base, head, tree_of(base_paths, head_paths), roots) == changes(option)


LIVE = {**FORBIDDEN_DEAD, "source_modules": ["shop.db", "shop.dead", "shop.api"]}


def test_one_dead_and_one_live_strike_in_one_field_stay_a_loosening_for_the_live_one_only():
    """LOOSE-19, LOOSE-20: the live name keeps the line; the dead name alone leaves none."""
    assert lines(LIVE, source_modules_of(["shop.db"])) == changes("source_modules")
    assert lines(LIVE, source_modules_of(["shop.db", "shop.api"])) == []


def test_a_dead_strike_beside_a_new_ignore_leaves_only_the_ignore():
    """LOOSE-19: the strike drops out of the comparison and the other lines stay."""
    ignoring = {**source_modules_of(["shop.db"]), "ignore_imports": ["shop.db -> shop.web"]}
    assert lines(FORBIDDEN_DEAD, ignoring) == ['import contract "guarded" ignores shop.db -> shop.web']


def test_a_name_that_both_sides_hold_is_no_strike_whatever_the_files_say():
    """LOOSE-19: only a name the head's list lacks is taken out of the base."""
    assert lines(FORBIDDEN_DEAD, FORBIDDEN_DEAD) == []


CONTAINED = {"type": "layers", "containers": ["shop"], "layers": ["metrics | grammar"]}


def test_a_dead_member_of_a_layer_leaves_the_layer_when_the_container_names_it():
    """LOOSE-19, LOOSE-20: a layers tail counts as `<container>.<tail>`."""
    head = {**CONTAINED, "layers": ["grammar"]}
    alive = BASE_PATHS | {"src/shop/metrics.py"}
    assert lines(CONTAINED, head, tree_of(BASE_PATHS, HEAD_PATHS)) == []
    assert lines(CONTAINED, head, tree_of(alive, alive)) == changes("layers")


@pytest.mark.parametrize(
    ("base_layers", "head_layers"),
    [(["web : dead"], ["web"]), (["web : api", "dead"], ["web : api"]), (["web | dead"], ["web"])],
    ids=["a layer left alone", "a whole layer of dead modules dropped", "an independent layer shrinks"],
)
def test_a_layers_contract_without_its_dead_modules_is_green(base_layers, head_layers):
    """LOOSE-19: the base's layers are compared without the dead tails."""
    assert lines({**LAYERS, "layers": base_layers}, {**LAYERS, "layers": head_layers}) == []


def test_a_tail_dead_in_one_container_and_present_in_another_stays_a_loosening():
    """LOOSE-20: a tail leaves only when its full name is gone in every container."""
    other = {"src/other/__init__.py", "src/other/web.py", "src/other/dead.py"}
    layers = {**LAYERS, "containers": ["shop", "other"]}
    tree = tree_of(BASE_PATHS | other, HEAD_PATHS | other)
    assert lines(layers, {**layers, "layers": ["web"]}, tree, ("shop", "other")) == changes("layers")


MOVED = {"src/shop/dead.py": "src/shop/new.py"}
RENAMED_CELLS = {
    "a module moved to a new file": (MOVED, HEAD_PATHS | {"src/shop/new.py"}),
    "a module moved outside every placed root": ({"src/shop/dead.py": "scripts/x.py"}, HEAD_PATHS | {"scripts/x.py"}),
}


@pytest.mark.parametrize("cell", RENAMED_CELLS)
def test_a_module_git_reads_as_renamed_stays_a_loosening(cell):
    """LOOSE-21: a struck module whose file moved to a file that is not empty stays `changes <list>`."""
    moves, head_paths = RENAMED_CELLS[cell]
    base, head = FIELD_CELLS["source_modules"]
    assert lines(base, head, tree_of(moves=moves, head_paths=head_paths)) == changes("source_modules")


def test_a_renamed_module_stays_a_loosening_even_when_the_head_lists_its_new_name():
    """LOOSE-21: the new name in the same field does not clear the strike."""
    head = source_modules_of(["shop.db", "shop.new"])
    tree = tree_of(moves=MOVED, head_paths=HEAD_PATHS | {"src/shop/new.py"})
    assert lines(FORBIDDEN_DEAD, head, tree) == changes("source_modules")


WEB_PACKAGE_BASE = BASE_PATHS | {"src/shop/web/__init__.py", "src/shop/web/views.py"}
WEB = {**FORBIDDEN_DEAD, "source_modules": ["shop.db", "shop.web"]}
WEB_STRUCK = {**WEB, "source_modules": ["shop.db"]}


@pytest.mark.parametrize(
    ("moves", "added", "expected"),
    [
        ({"src/shop/web/views.py": "src/shop/site/views.py"}, {"src/shop/site/views.py"}, changes("source_modules")),
        ({"src/shop/web/views.py": "src/shop/site/views.py", "src/shop/web/__init__.py": "src/shop/other/__init__.py"},
         {"src/shop/site/views.py", "src/shop/other/__init__.py"}, changes("source_modules")),
        ({}, {"src/shop/unrelated.py"}, []),
    ],
    ids=["a package whose views moved", "a package whose files moved to two folders", "a package deleted beside an unrelated file"],
)
def test_a_package_counts_as_renamed_when_any_file_under_its_folder_moved(moves, added, expected):
    """LOOSE-19, LOOSE-21: the package's own file or any file below its folder is a rename source."""
    removed = {"src/shop/dead.py", "src/shop/web.py", "src/shop/web/__init__.py", "src/shop/web/views.py"}
    head_paths = (WEB_PACKAGE_BASE - removed) | added
    assert lines(WEB, WEB_STRUCK, tree_of(WEB_PACKAGE_BASE, head_paths, moves)) == expected


def test_a_rename_is_matched_below_the_directory_the_base_placed_the_root_in():
    """LOOSE-21: `shop/` moved to `src/shop/` with `web.py` renamed on the way still reads `shop.web` as renamed."""
    base_paths = {"shop/__init__.py", "shop/web.py", "shop/db.py"}
    head_paths = {"src/shop/__init__.py", "src/shop/site.py", "src/shop/db.py"}
    moves = {"shop/__init__.py": "src/shop/__init__.py", "shop/web.py": "src/shop/site.py", "shop/db.py": "src/shop/db.py"}
    assert lines(WEB, WEB_STRUCK, tree_of(base_paths, head_paths, moves)) == changes("source_modules")


def test_a_rename_to_an_empty_file_is_no_rename():
    """LOOSE-19, LOOSE-21: git pairs a deleted empty `__init__.py` with a new empty file; only a target with code counts."""
    base_paths = {"src/shop/__init__.py", "src/shop/db.py", "src/shop/dead/__init__.py", "src/shop/dead/logic.py"}
    head_paths = {"src/shop/__init__.py", "src/shop/db.py", "src/shop/py.typed"}
    dead = {**FORBIDDEN_DEAD, "source_modules": ["shop.db", "shop.dead"]}
    pairing = {"src/shop/dead/__init__.py": "src/shop/py.typed"}
    assert lines(dead, source_modules_of(["shop.db"]), tree_of(base_paths, head_paths, pairing, {"src/shop/py.typed"})) == []
    moved = {**pairing, "src/shop/dead/logic.py": "src/shop/other.py"}
    assert lines(dead, source_modules_of(["shop.db"]),
                 tree_of(base_paths, head_paths | {"src/shop/other.py"}, moved, {"src/shop/py.typed"})) == changes("source_modules")


NO_CONTAINER = {"type": "layers", "layers": ["web : dead"]}
STRUCK_CASES = [
    ({"pyproject.toml": toml(FORBIDDEN_DEAD)}, {"pyproject.toml": toml(source_modules_of(["shop.db"]))}, {"shop.dead"}),
    ({"pyproject.toml": toml(FORBIDDEN_DEAD)}, {"pyproject.toml": toml(FORBIDDEN_DEAD)}, set()),
    ({"pyproject.toml": toml(source_modules_of(["shop.db", "shop.*"]))}, {"pyproject.toml": toml(source_modules_of(["shop.db"]))}, set()),
    ({"pyproject.toml": toml({**LAYERS, "containers": ["shop", "other"]})},
     {"pyproject.toml": toml({**LAYERS, "containers": ["shop", "other"], "layers": ["web"]})}, {"shop.dead", "other.dead"}),
    ({"pyproject.toml": toml({**LAYERS, "layers": ["web | (dead)"]})}, {"pyproject.toml": toml({**LAYERS, "layers": ["web"]})}, {"shop.dead"}),
    ({"pyproject.toml": toml(NO_CONTAINER)}, {"pyproject.toml": toml({**NO_CONTAINER, "layers": ["web"]})}, {"dead"}),
    ({"pyproject.toml": toml(FORBIDDEN_DEAD)}, {"pyproject.toml": toml({**FORBIDDEN_DEAD, "type": "independence", "modules": ["shop.db"]})}, set()),
    ({"pyproject.toml": toml(FORBIDDEN_DEAD)}, {}, set()),
    ({}, {"pyproject.toml": toml(FORBIDDEN_DEAD)}, set()),
    ({"pyproject.toml": toml(FORBIDDEN_DEAD)}, {".importlinter": contracts_ini({"guarded": source_modules_of(["shop.db"])})}, {"shop.dead"}),
]


@pytest.mark.parametrize(
    ("base", "head", "names"), STRUCK_CASES,
    ids=["a module struck", "an unchanged name", "a wildcard", "a tail per container", "an optional tail",
         "a tail without containers", "a contract of another type", "no config at the head", "no config at the base",
         "a config moved to another file"],
)
def test_the_struck_modules_are_the_names_the_head_lacks_in_each_list(base, head, names):
    """LOOSE-19, LOOSE-20: the names that decide whether the files are read, never a wildcard."""
    assert architecture.struck_modules(architecture.contracts_of(base), architecture.contracts_of(head)) == names
