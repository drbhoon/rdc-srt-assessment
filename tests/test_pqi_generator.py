"""PQI forms: 30 SRTs, 3 per competency, stratified, AFI=Yes on target, reproducible."""
import copy
from collections import Counter, defaultdict

import pytest

from pqi_generator import PqiGenerationError, generate_form
from test_pqi_master import master  # noqa: F401  (module-scoped fixture)


def test_every_form_meets_the_locked_rules(master):
    index = {s["srt_id"]: s for s in master["srts"]}
    for seed in range(1500):
        form = generate_form(master, seed=seed)
        srts = [index[i] for i in form["srt_ids"]]
        assert len(srts) == 30 and len(set(form["srt_ids"])) == 30
        assert Counter(s["primary_competency"] for s in srts) == {f"C{i}": 3 for i in range(1, 11)}
        afi = sum(1 for s in srts if s["afi_applicability"] == "Yes")
        assert 18 <= afi <= 22 and afi == form["afi_yes"] and form["within_target"]
        by_comp = defaultdict(list)
        for s in srts:
            by_comp[s["primary_competency"]].append(s)
        for trio in by_comp.values():
            assert any(s["decision_applicable"] == "Yes" for s in trio)
            assert len({s["demand_type"] for s in trio}) == 3


def test_a_seed_reproduces_its_form(master):
    assert generate_form(master, seed=424242) == generate_form(master, seed=424242)


def test_forms_differ(master):
    forms = {tuple(sorted(generate_form(master, seed=s)["srt_ids"])) for s in range(300)}
    assert len(forms) == 300


def test_unreachable_target_falls_back_to_the_closest_form_above_the_floor(master):
    strict = copy.deepcopy(master)
    strict["afi_yes_target"] = [29, 30]           # a balanced form holds at most 28
    form = generate_form(strict, seed=7, max_attempts=3000)
    assert form["within_target"] is False and form["afi_yes"] >= strict["afi_yes_floor"]


def test_unreachable_floor_is_refused(master):
    impossible = copy.deepcopy(master)
    impossible["afi_yes_target"], impossible["afi_yes_floor"] = [29, 30], 29
    with pytest.raises(PqiGenerationError):
        generate_form(impossible, seed=1, max_attempts=200)
