from quality.rules import Area, Rule


def test_every_rule_has_an_area():
    assert all(isinstance(rule.area, Area) for rule in Rule)


def test_every_area_holds_a_rule():
    assert {rule.area for rule in Rule} == set(Area)
