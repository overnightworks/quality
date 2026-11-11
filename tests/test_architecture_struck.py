import pytest

from quality import architecture
from quality.architecture import PullTree
from quality.ratchet import Renames
from tests.support.architecture import GUARDED_CHANGES, contracts_ini, contracts_toml, loosened, toml

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


INI = ".importlinter"
BASE_INI = {INI: contracts_ini({"guarded": FORBIDDEN_DEAD})}
HEAD_INI = {INI: contracts_ini({"guarded": {**FORBIDDEN_DEAD, "source_modules": ["shop.db"]}})}


@pytest.mark.parametrize(
    ("base", "head"),
    [
        (BASE_INI, HEAD_INI),
        ({PYPROJECT: toml(FORBIDDEN_DEAD)}, HEAD_INI),
        (BASE_INI, {PYPROJECT: toml({**FORBIDDEN_DEAD, "source_modules": ["shop.db"]})}),
    ],
    ids=["INI to INI", "TOML to INI", "INI to TOML"],
)
@pytest.mark.parametrize(("tree", "expected"), [(tree_of(), []), (tree_of(head_paths=BASE_PATHS), changes("source_modules"))],
                         ids=["a dead strike", "a kept strike"])
def test_a_strike_is_judged_by_the_files_whichever_file_holds_each_side_contracts(base, head, tree, expected):
    """LOOSE-19, LOOSE-20: the config file format of either side does not change what a strike is judged by."""
    assert loosened(base, head, tree) == expected


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


NEW_LISTED = {
    "source_modules": {**FORBIDDEN_DEAD, "source_modules": ["shop.db", "shop.new"]},
    "forbidden_modules": {**FORBIDDEN_DEAD, "source_modules": ["shop.db"], "forbidden_modules": ["shop.web", "shop.new"]},
    "modules": {"type": "independence", "modules": ["shop.web", "shop.api", "shop.new"]},
    "protected_modules": {"type": "protected", "protected_modules": ["shop.db", "shop.new"], "allowed_importers": ["shop.api"]},
    "containers": {"type": "layers", "containers": ["shop", "shop.new"], "layers": ["web : api"]},
    "layers": {**LAYERS, "layers": ["web : new"]},
}
RENAMED_TO_NEW = tree_of(moves=MOVED, head_paths=HEAD_PATHS | {"src/shop/new.py"})


@pytest.mark.parametrize("option", NEW_LISTED)
def test_a_renamed_module_is_no_loosening_when_the_same_list_names_its_new_module(option):
    """LOOSE-22: the renamed module in the same field of the same contract clears the strike, in each of the six lists."""
    assert lines(FIELD_CELLS[option][0], NEW_LISTED[option], RENAMED_TO_NEW) == []


def test_a_renamed_module_in_a_layers_contract_without_containers_is_named_by_its_tail_alone():
    """LOOSE-22: a tail without a container is the whole dotted name."""
    base, head = {"type": "layers", "layers": ["web : shop.dead"]}, {"type": "layers", "layers": ["web : shop.new"]}
    assert lines(base, head, RENAMED_TO_NEW) == []


def test_a_renamed_module_listed_in_another_list_of_the_same_contract_stays_a_loosening():
    """LOOSE-22, LOOSE-21: only the same field clears the strike."""
    head = {**FORBIDDEN_DEAD, "source_modules": ["shop.db"], "forbidden_modules": ["shop.web", "shop.new"]}
    assert lines(FORBIDDEN_DEAD, head, RENAMED_TO_NEW) == changes("source_modules")


def test_a_renamed_module_listed_in_another_contract_stays_a_loosening():
    """LOOSE-22, LOOSE-21: only the same contract clears the strike."""
    other = {"type": "forbidden", "source_modules": ["shop.api"], "forbidden_modules": ["shop.web"]}
    base = {"guarded": FORBIDDEN_DEAD, "other": other}
    head = {"guarded": source_modules_of(["shop.db"]), "other": {**other, "source_modules": ["shop.api", "shop.new"]}}
    found = architecture.contract_loosenings(architecture.contracts_of({PYPROJECT: contracts_toml(base)}),
                                             architecture.contracts_of({PYPROJECT: contracts_toml(head)}), RENAMED_TO_NEW)
    assert found == changes("source_modules")


def test_a_renamed_module_stays_a_loosening_without_a_tree_whatever_the_head_lists():
    """LOOSE-22, LOOSE-20: without a tree the new name clears nothing."""
    assert lines(FORBIDDEN_DEAD, NEW_LISTED["source_modules"], None) == changes("source_modules")


FILE_BASE = BASE_PATHS
PACKAGE_BASE = HEAD_PATHS | {"src/shop/dead/__init__.py"}


@pytest.mark.parametrize("listed", [True, False], ids=["listed", "unlisted"])
@pytest.mark.parametrize(
    ("base_paths", "source", "target", "candidate"),
    [
        (FILE_BASE, "src/shop/dead.py", "src/shop/new.py", "shop.new"),
        (FILE_BASE, "src/shop/dead.py", "src/shop/new/__init__.py", "shop.new"),
        (PACKAGE_BASE, "src/shop/dead/__init__.py", "src/shop/new/__init__.py", "shop.new"),
        (PACKAGE_BASE, "src/shop/dead/__init__.py", "src/shop/new.py", "shop.new"),
    ],
    ids=["file to file", "file to package", "package to package", "package to file"],
)
def test_a_module_moved_as_a_file_or_a_package_is_cleared_by_exactly_its_new_name(base_paths, source, target, candidate, listed):
    """LOOSE-22, LOOSE-21: `<x>.py` and `<x>/__init__.py` both name `x`; unlisted the strike stays red."""
    head_paths = (base_paths - {source}) | {target}
    head = source_modules_of(["shop.db", candidate] if listed else ["shop.db"])
    assert lines(FORBIDDEN_DEAD, head, tree_of(base_paths, head_paths, {source: target})) == ([] if listed else changes("source_modules"))


FOLDER_BASE = HEAD_PATHS | {"src/shop/pages/views.py", "src/shop/pages/forms.py"}
PAGES = {**FORBIDDEN_DEAD, "source_modules": ["shop.db", "shop.pages"]}
SITE_MOVES = {"src/shop/pages/views.py": "src/shop/site/views.py", "src/shop/pages/forms.py": "src/shop/site/forms.py"}


def web_tree(moves):
    head_paths = (FOLDER_BASE - set(moves)) | set(moves.values())
    return tree_of(FOLDER_BASE, head_paths, moves)


@pytest.mark.parametrize(
    ("moves", "listed", "expected"),
    [
        (SITE_MOVES, ["shop.site"], []),
        (SITE_MOVES, ["shop.site.views"], changes("source_modules")),
        ({"src/shop/pages/views.py": "src/shop/xviews.py", "src/shop/pages/forms.py": "src/shop/xforms.py"}, ["shop.x"],
         changes("source_modules")),
        ({**SITE_MOVES, "src/shop/pages/forms.py": "src/shop/other/forms.py"}, ["shop.site", "shop.other"], changes("source_modules")),
        ({"src/shop/pages/views.py": "src/shop/site/views.py", "src/shop/pages/forms.py": "scripts/forms.py"}, ["shop.site"],
         changes("source_modules")),
    ],
    ids=["a folder renamed, two sources naming one candidate", "only a module below the new folder listed", "a file renamed on the way (no candidate)",
         "files moved to two folders, both listed", "one file moved outside every root"],
)
def test_a_folder_is_cleared_by_the_folder_its_files_moved_to_and_by_no_other_name(moves, listed, expected):
    """LOOSE-22, LOOSE-21: the source's parts below the folder equal the target's last parts, the rest names the module;
    sources that name the same candidate leave it the renamed module."""
    assert lines(PAGES, source_modules_of(["shop.db", *listed]), web_tree(moves)) == expected


@pytest.mark.parametrize(
    ("target", "listed"),
    [
        ("src/shop/data.txt", "shop.data"),
        ("scripts/x.py", "scripts.x"),
        ("src/shop.py", "shop"),
        ("src/shop/a.b.py", "shop.a.b"),
        ("src/shop/new-x.py", "shop.new-x"),
    ],
    ids=["a data file", "outside every placed root", "beside the root package", "a dot in the file name", "a dash in the file name"],
)
def test_a_target_that_is_no_module_below_a_placed_root_names_no_candidate(target, listed):
    """LOOSE-22, LOOSE-21: a data file, a file outside the roots, a file beside the root package and a file whose name is
    no importable module name stay red when listed."""
    tree = tree_of(moves={"src/shop/dead.py": target}, head_paths=HEAD_PATHS | {target})
    assert lines(FORBIDDEN_DEAD, source_modules_of(["shop.db", listed]), tree) == changes("source_modules")


@pytest.mark.parametrize(("listed", "expected"), [(["shop.db", "shop"], []), (["shop.db"], changes("source_modules"))],
                         ids=["listed", "unlisted"])
def test_a_module_moved_onto_the_root_packages_init_file_names_the_root_package(listed, expected):
    """LOOSE-22, LOOSE-21: the root package's own name is the candidate, cleared only by that name."""
    tree = tree_of(moves={"src/shop/dead.py": "src/shop/__init__.py"})
    assert lines(FORBIDDEN_DEAD, source_modules_of(listed), tree) == expected


def moved_tree(moves):
    """The shop package with only the files of `moves` moved, each to its target."""
    package = {"src/shop/__init__.py"}
    return tree_of(package | set(moves), package | set(moves.values()), moves)


FOLDER_MOVES = {f"src/shop/old/{name}": f"src/shop/new/{name}" for name in ("__init__.py", "p.py", "q.py")}
LAYER_MOVES = {"src/shop/a.py": "src/shop/na.py"}
RENAMED_LAYERS = [
    ({"containers": ["shop.old"], "layers": ["p", "q"]}, {"containers": ["shop.new"], "layers": ["q", "p"]}, FOLDER_MOVES, changes("layers")),
    ({"layers": ["shop.old.p", "shop.old.q"]}, {"layers": ["shop.new.q", "shop.new.p"]}, FOLDER_MOVES, changes("layers")),
    ({"containers": ["shop"], "layers": ["web", "db", "a"]}, {"containers": ["shop"], "layers": ["na", "web", "db"]}, LAYER_MOVES,
     changes("layers")),
    ({"containers": ["shop"], "layers": ["web | a"]}, {"containers": ["shop"], "layers": ["web : na"]}, LAYER_MOVES, changes("layers")),
    ({"containers": ["shop.old"], "layers": ["p", "q"]}, {"containers": ["shop.new"], "layers": ["p", "q"]}, FOLDER_MOVES, []),
    ({"layers": ["shop.old.p", "shop.old.q"]}, {"layers": ["shop.new.p", "shop.new.q"]}, FOLDER_MOVES, []),
    ({"containers": ["shop"], "layers": ["web", "db", "a"]}, {"containers": ["shop"], "layers": ["web", "db", "na"]}, LAYER_MOVES, []),
    ({"containers": ["shop"], "layers": ["web | a"]}, {"containers": ["shop"], "layers": ["web | na"]}, LAYER_MOVES, []),
]


@pytest.mark.parametrize(("base", "head", "moves", "expected"), RENAMED_LAYERS,
                         ids=["a renamed container with its layers reversed", "every module renamed and the layers reversed",
                              "a renamed layer moved to the top", "a renamed layer moved from `|` to `:`",
                              "a renamed container", "every module renamed", "a renamed layer kept in its place",
                              "a renamed sibling kept in its group"])
def test_a_renamed_module_keeps_the_place_of_the_struck_name_in_a_layers_contract(base, head, moves, expected):
    """LOOSE-22, LOOSE-20: the head's layers name the module at the struck name's place, or they differ and read
    `changes layers`."""
    assert lines({"type": "layers", **base}, {"type": "layers", **head}, moved_tree(moves)) == expected


def test_a_wildcard_listed_at_the_head_does_not_name_the_renamed_module():
    """LOOSE-22, LOOSE-21: only the exact dotted name of the renamed module counts."""
    assert lines(FORBIDDEN_DEAD, source_modules_of(["shop.db", "shop.*"]), RENAMED_TO_NEW) == changes("source_modules")


EMPTY_BASE = {"src/shop/__init__.py", "src/shop/db.py", "src/shop/dead/__init__.py", "src/shop/dead/logic.py"}
EMPTY_MOVES = {"src/shop/dead/__init__.py": "src/shop/py.typed", "src/shop/dead/logic.py": "src/shop/other/logic.py"}


def test_an_empty_target_neither_names_a_candidate_nor_blocks_one():
    """LOOSE-22, LOOSE-21: an empty `__init__.py` paired with a new empty file leaves the module's other file to name it."""
    head_paths = {"src/shop/__init__.py", "src/shop/db.py", "src/shop/py.typed", "src/shop/other/logic.py"}
    tree = tree_of(EMPTY_BASE, head_paths, EMPTY_MOVES, {"src/shop/py.typed"})
    dead = {**FORBIDDEN_DEAD, "source_modules": ["shop.db", "shop.dead"]}
    assert lines(dead, source_modules_of(["shop.db", "shop.other"]), tree) == []
    assert lines(dead, source_modules_of(["shop.db"]), tree) == changes("source_modules")


def test_a_root_package_at_the_top_of_the_repository_names_its_renamed_module_without_a_directory():
    """LOOSE-22: a module moved inside a package at the repository top is read back from the top."""
    base, head = {*TOP}, {"shop/__init__.py", "shop/web.py", "shop/new.py"}
    tree = tree_of(base, head, {"shop/dead.py": "shop/new.py"})
    assert lines(FORBIDDEN_DEAD, source_modules_of(["shop.db", "shop.new"]), tree) == []


@pytest.mark.parametrize(("listed", "expected"), [(["shop.site"], []), ([], changes("source_modules"))],
                         ids=["listed", "unlisted"])
def test_a_rename_matched_at_the_base_directory_names_its_module_at_the_head_directory(listed, expected):
    """LOOSE-22, LOOSE-21: `shop/` moved to `src/shop/` with `web.py` renamed to `site.py` is read as `shop.site`."""
    base_paths = {"shop/__init__.py", "shop/web.py", "shop/db.py"}
    head_paths = {"src/shop/__init__.py", "src/shop/site.py", "src/shop/db.py"}
    moves = {"shop/__init__.py": "src/shop/__init__.py", "shop/web.py": "src/shop/site.py", "shop/db.py": "src/shop/db.py"}
    web = {**WEB, "source_modules": ["shop.db", "shop.web"]}
    assert lines(web, source_modules_of(["shop.db", *listed]), tree_of(base_paths, head_paths, moves)) == expected


def test_a_renamed_module_of_a_root_package_the_base_cannot_place_stays_a_loosening_when_listed():
    """LOOSE-22, LOOSE-20: the base cannot place `shop`, so the strike is present however the head lists it."""
    tree = tree_of(BASE_PATHS - {"src/shop/__init__.py"}, HEAD_PATHS | {"src/shop/new.py"}, MOVED)
    assert lines(FORBIDDEN_DEAD, NEW_LISTED["source_modules"], tree) == changes("source_modules")


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


KEPT = {"type": "forbidden", "source_modules": ["shop.db"], "forbidden_modules": ["shop.web"]}
REMOVED = ['import contract "gone" removed']


def removal(gone, tree="deleted", roots=("shop",), kept=None, head_kept=None):
    """The loosenings of a head that drops the contract `gone`, keeps `kept` and changes it to `head_kept`."""
    tree = tree_of() if tree == "deleted" else tree
    kept = {"kept": KEPT, **(kept or {})}
    base = {PYPROJECT: contracts_toml({**kept, "gone": gone}, roots)}
    head = {PYPROJECT: contracts_toml({**kept, **(head_kept or {})}, roots)}
    return loosened(base, head, tree)


def forbidden(source, forbidden_modules):
    return {"type": "forbidden", "source_modules": source, "forbidden_modules": forbidden_modules}


GONE_SOURCE = forbidden(["shop.dead"], ["shop.web"])
REMOVAL_FIELD_CELLS = {
    "source_modules": GONE_SOURCE,
    "forbidden_modules": forbidden(["shop.db"], ["shop.dead"]),
    "modules": {"type": "independence", "modules": ["shop.dead"]},
    "protected_modules": {"type": "protected", "protected_modules": ["shop.dead"], "allowed_importers": ["shop.api"]},
    "containers": {"type": "layers", "containers": ["shop.dead"], "layers": ["web : api"]},
    "layers": {"type": "layers", "containers": ["shop"], "layers": ["dead"]},
}


@pytest.mark.parametrize("option", REMOVAL_FIELD_CELLS)
def test_a_contract_removed_with_one_list_of_only_deleted_modules_is_no_loosening(option):
    """LOOSE-23: each of the six lists, as the only dead list of a removed contract, lets the removal go."""
    assert removal(REMOVAL_FIELD_CELLS[option]) == []


@pytest.mark.parametrize("gone", [GONE_SOURCE, forbidden(["shop.db"], ["shop.web"])], ids=["a dead contract", "a live contract"])
def test_a_contract_removal_stays_a_loosening_when_no_tree_was_read(gone):
    """LOOSE-23, LOOSE-20: without a tree nothing counts as missing."""
    assert removal(gone, None) == REMOVED


@pytest.mark.parametrize(
    ("gone", "expected"),
    [
        (forbidden(["shop.db"], ["shop.web"]), REMOVED),
        (forbidden(["shop.dead", "shop.db"], ["shop.web"]), REMOVED),
        (forbidden([], ["shop.web"]), REMOVED),
        ({"type": "independence", "modules": []}, REMOVED),
        (forbidden(["shop.*"], ["shop.web"]), REMOVED),
        (forbidden(["shop.dead", "shop.*"], ["shop.web"]), REMOVED),
        (forbidden(["shop.dead", "shop.*"], ["shop.dead"]), []),
        (forbidden(["requests"], ["shop.web"]), REMOVED),
        (forbidden(["shop.dead"], ["shop.db"]), []),
    ],
    ids=["every list live", "one list mixing dead and live", "a list empty beside a live one", "every list empty",
         "a list of one wildcard", "a dead name beside a wildcard", "a dead name beside a wildcard, another list dead",
         "an external name only", "one dead list beside a live one"],
)
def test_a_contract_removal_is_no_loosening_only_when_one_list_names_deleted_modules_and_nothing_else(gone, expected):
    """LOOSE-23, LOOSE-20: a live, mixed, empty, wildcard or external list does not let the removal go."""
    assert removal(gone) == expected


MOVED_SITE = {"src/shop/web/views.py": "src/shop/site/views.py", "src/shop/web/__init__.py": "src/shop/other/__init__.py"}
SITE_HEAD = (WEB_PACKAGE_BASE - {"src/shop/web/__init__.py", "src/shop/web/views.py", "src/shop/web.py", "src/shop/dead.py"}
             | {"src/shop/site/views.py", "src/shop/other/__init__.py"})
RENAMED_HEAD = HEAD_PATHS | {"src/shop/new.py"}


@pytest.mark.parametrize(
    ("gone", "tree", "head_kept"),
    [
        (GONE_SOURCE, tree_of(moves=MOVED, head_paths=RENAMED_HEAD), None),
        (GONE_SOURCE, tree_of(moves=MOVED, head_paths=RENAMED_HEAD), {"kept": {**KEPT, "source_modules": ["shop.db", "shop.new"]}}),
        (forbidden(["shop.web"], ["shop.api"]), tree_of(WEB_PACKAGE_BASE, SITE_HEAD, MOVED_SITE), None),
    ],
    ids=["a renamed module", "a renamed module, its new name listed in a kept contract", "a package whose files moved to two folders"],
)
def test_a_contract_removal_stays_a_loosening_when_git_reads_its_module_as_renamed(gone, tree, head_kept):
    """LOOSE-23, LOOSE-21: a renamed module counts as live, a removal has no head list to name its new module."""
    assert removal(gone, tree, head_kept=head_kept) == REMOVED


@pytest.mark.parametrize(
    ("base_paths", "head_paths"),
    [(BASE_PATHS, HEAD_PATHS - {"src/shop/__init__.py"}), (BASE_PATHS - {"src/shop/__init__.py"}, HEAD_PATHS)],
    ids=["a root package the head cannot place", "a root package the base cannot place"],
)
def test_a_contract_removal_stays_a_loosening_when_a_root_package_cannot_be_placed(base_paths, head_paths):
    """LOOSE-23, LOOSE-20: a name of a root package the files cannot place counts as present."""
    assert removal(GONE_SOURCE, tree_of(base_paths, head_paths)) == REMOVED


OTHER_PACKAGE = {"src/other/__init__.py", "src/other/web.py"}
TAIL_ELSEWHERE = OTHER_PACKAGE | {"src/other/dead.py"}


@pytest.mark.parametrize(
    ("other", "expected"), [(OTHER_PACKAGE, []), (TAIL_ELSEWHERE, REMOVED)],
    ids=["a tail dead in every container", "a tail dead in one container and present in another"],
)
def test_a_removed_layers_contract_needs_its_tail_dead_in_every_container(other, expected):
    """LOOSE-23, LOOSE-20: a layers tail counts once per base container."""
    gone = {"type": "layers", "containers": ["shop", "other"], "layers": ["dead"]}
    assert removal(gone, tree_of(BASE_PATHS | other, HEAD_PATHS | other), ("shop", "other")) == expected


SUB_PACKAGE = {"src/shop/sub/__init__.py", "src/shop/sub/dead.py"}
OTHER_LIVE = {"src/other/__init__.py", "src/other/web.py"}
SUB_RENAMED = {"src/shop/sub/__init__.py": "src/shop/other/__init__.py"}


@pytest.mark.parametrize(
    ("containers", "tree", "expected"),
    [
        (["shop"], tree_of(BASE_PATHS, HEAD_PATHS), REMOVED),
        (["shop.sub"], tree_of(BASE_PATHS | SUB_PACKAGE, HEAD_PATHS), []),
        (["shop.sub"], tree_of(BASE_PATHS | SUB_PACKAGE, HEAD_PATHS | {"src/shop/other/__init__.py"}, moves=SUB_RENAMED), REMOVED),
        (["shop.sub"], tree_of(BASE_PATHS | SUB_PACKAGE, HEAD_PATHS | {"src/shop/other/__init__.py"}, moves=SUB_RENAMED,
                               empty={"src/shop/other/__init__.py"}), []),
        (["shop.sub", "other"], tree_of(BASE_PATHS | SUB_PACKAGE | OTHER_LIVE, HEAD_PATHS | OTHER_LIVE), REMOVED),
    ],
    ids=["a container with files at the head", "a container with no file at the head",
         "a renamed container with no file at the head", "a container renamed to an empty file", "one of two containers with files at the head"],
)
def test_a_removed_exhaustive_layers_contract_goes_only_when_none_of_its_containers_has_a_file_or_a_rename_at_the_head(containers, tree, expected):
    """LOOSE-23: an exhaustive contract still forbids every undeclared child of a live container."""
    gone = {"type": "layers", "containers": containers, "layers": ["dead"], "exhaustive": True}
    assert removal(gone, tree, ("shop", "other")) == expected


def test_a_removed_layers_contract_without_containers_stays_a_loosening():
    """LOOSE-23, LOOSE-20: a tail with no container is a package name the files cannot place."""
    assert removal({"type": "layers", "layers": ["dead"]}) == REMOVED


METRICS_BASE = {"src/shop/__init__.py", "src/shop/items.py", "src/shop/grammar.py", "src/shop/metrics.py"}
METRICS_LAYERS = {"type": "layers", "containers": ["shop"], "layers": ["metrics | grammar"]}


@pytest.mark.parametrize(
    ("head_paths", "expected"),
    [(METRICS_BASE - {"src/shop/metrics.py"}, []), (METRICS_BASE, ['import contract "Layers" changes layers', *REMOVED])],
    ids=["metrics deleted", "metrics kept"],
)
def test_a_deleted_layer_member_and_the_contract_that_only_guarded_it_leave_together_only_when_the_module_is_deleted(head_paths, expected):
    """LOOSE-23, LOOSE-19: the strike and the removal of one deleted module read the same files."""
    layers = {"Layers": METRICS_LAYERS}
    gone = forbidden(["shop.items"], ["shop.metrics"])
    assert removal(gone, tree_of(METRICS_BASE, head_paths), kept=layers, head_kept={"Layers": {**METRICS_LAYERS, "layers": ["grammar"]}}) == expected


def test_only_the_live_removal_of_two_leaves_a_line():
    """LOOSE-23: each removed contract is judged on its own lists."""
    base = {PYPROJECT: contracts_toml({"kept": KEPT, "gone": GONE_SOURCE, "live": KEPT})}
    head = {PYPROJECT: contracts_toml({"kept": KEPT})}
    assert loosened(base, head, tree_of()) == ['import contract "live" removed']


def test_a_dead_removal_beside_a_live_strike_leaves_only_the_strike():
    """LOOSE-23, LOOSE-20: the removal's dead list does not clear a strike of a name the files hold."""
    base = {PYPROJECT: contracts_toml({"guarded": LIVE, "gone": GONE_SOURCE})}
    head = {PYPROJECT: contracts_toml({"guarded": source_modules_of(["shop.db", "shop.dead"])})}
    assert loosened(base, head, tree_of()) == changes("source_modules")


def test_the_whole_config_removed_stays_one_line_whatever_the_removed_contracts_hold():
    """LOOSE-23, LOOSE-8: no head config reads no tree."""
    base = {PYPROJECT: contracts_toml({"kept": KEPT, "gone": GONE_SOURCE})}
    assert loosened(base, {}, tree_of()) == [f"import-linter config in {PYPROJECT} removed"]


REMOVED_CASES = [
    ({PYPROJECT: contracts_toml({"kept": KEPT, "gone": GONE_SOURCE})}, {PYPROJECT: contracts_toml({"kept": KEPT})}, {"shop.dead", "shop.web"}),
    ({PYPROJECT: contracts_toml({"kept": KEPT, "gone": forbidden(["shop.dead", "shop.*"], ["shop.web"])})},
     {PYPROJECT: contracts_toml({"kept": KEPT})}, {"shop.dead", "shop.web"}),
    ({PYPROJECT: contracts_toml({"kept": KEPT, "gone": {**LAYERS, "containers": ["shop", "other"]}})},
     {PYPROJECT: contracts_toml({"kept": KEPT})}, {"shop", "other", "shop.web", "shop.dead", "other.web", "other.dead"}),
    ({PYPROJECT: contracts_toml({"kept": KEPT, "gone": GONE_SOURCE})}, {}, set()),
]


@pytest.mark.parametrize(
    ("base", "head", "names"), REMOVED_CASES,
    ids=["a removed contract", "a removed contract with a wildcard", "a removed layers contract per container", "no config at the head"],
)
def test_the_struck_modules_hold_the_wildcard_free_names_of_a_removed_contract(base, head, names):
    """LOOSE-23, LOOSE-24: a removed contract's names decide that the files are read."""
    assert architecture.struck_modules(architecture.contracts_of(base), architecture.contracts_of(head)) == names
