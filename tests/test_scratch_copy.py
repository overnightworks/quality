from quality import clones
from quality.ratchet import ClonePair, Place

from test_clones import function

def test_scratch_copy():
    """#113 E2: lines inserted above the copy and an edited neighbour leave the pair's name unchanged."""
    first, second = function("first", "a", "b"), function("second", "c", "d")
    neighbour = "def neighbour(a):\n    return a\n"
    edited_neighbour = "def neighbour(a, b):\n    c = a\n    return b\n"
    before = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": f"{neighbour}\n\n{second}".encode()}).pairs
    after = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": f"import os\n\n\n{edited_neighbour}\n\n{second}\nx = 1\n".encode()}).pairs
    assert before == after == {ClonePair(Place("src/a.py", "first"), Place("src/b.py", "second"))}
