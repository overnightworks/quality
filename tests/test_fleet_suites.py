from quality.fleet_suites import cells
from quality.suites import Layer, Suites


def test_a_measured_repository_reads_its_counts_with_thousands_separators_and_its_unread_files():
    """FLEET-119, FLEET-124: the layers, then Skipped and Fixme, and the note names how many files were not read."""
    suites = Suites({Layer.UNIT_INTEGRATION: 4100, Layer.FLOW: 440, Layer.LIVE: 0}, skipped=11, fixme=1, unread=2)
    assert cells("o/a", suites) == ("o/a", "4,100", "440", "0", "11", "1", "2 files not read")


def test_a_repository_whose_files_were_all_read_has_no_note():
    """FLEET-124: no unread file, no note."""
    assert cells("o/a", Suites({layer: 0 for layer in Layer}, skipped=0, fixme=0, unread=0))[-1] == ""
