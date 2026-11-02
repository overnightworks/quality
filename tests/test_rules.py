from quality import ratchet
from quality.rules import Area, Rule


def test_every_rule_has_an_area():
    assert all(isinstance(rule.area, Area) for rule in Rule)


def test_every_area_holds_a_rule():
    assert {rule.area for rule in Rule} == set(Area)


def test_the_debt_help_line_explains_the_ceiling_file():
    assert f"debt ceiling ({ratchet.BASELINE_PATH})" in Area.DEBT.help_line
