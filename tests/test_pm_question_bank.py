"""Plant Manager question bank and 30-question form selection."""
import copy
import json
import random
from collections import Counter

import main
import pm_fakes as fk
from conftest import ROOT
from question_bank import REQUIRED_COMPETENCIES, get_session_questions, load_questions

PM_MASTER = ROOT / "data" / "RDC_SRT_Master_100.xlsx"
PM_MASTER_SHA256 = "00cf6dc1564efe1e3d50ce5af4b65e36e60bdeafd17e3a734f7665a93742bba6"

QUESTION_KEYS = {"question_number", "srt_id", "primary_competency", "secondary_competency", "situation"}


def test_pm_master_workbook_is_the_deployed_file():
    assert fk.sha256(PM_MASTER.read_bytes()) == PM_MASTER_SHA256


def test_pm_competencies_are_unchanged():
    assert REQUIRED_COMPETENCIES == [
        "Integrity & Trust",
        "Preventive Maintenance & Asset Care",
        "Planning, Organizing & Coordination",
        "Operational Discipline & SARTAJ Ownership",
        "Communication & Assertiveness",
        "Team Orientation & Delegation",
        "Customer Orientation & Relationship Handling",
        "Vendor & External Stakeholder Management",
        "Cost & Resource Responsibility",
        "Functional Knowledge & Multiskilling",
    ]


def test_pm_bank_loads_as_deployed(golden):
    bank = main.questions_db
    assert load_questions(str(PM_MASTER)) == bank
    assert sorted(bank) == sorted(REQUIRED_COMPETENCIES)
    assert all(len(qs) == 10 for qs in bank.values())
    golden("pm_question_bank", {
        "competency_order": list(bank),
        "srt_ids":          {c: [q["srt_id"] for q in qs] for c, qs in bank.items()},
        "content_sha256":   fk.sha256(json.dumps(bank, sort_keys=True, ensure_ascii=False)),
    })


def test_pm_selection_for_fixed_seeds_is_unchanged(golden):
    picks = {}
    for seed in (0, 1, 7, 42, 2026):
        random.seed(seed)
        picks[str(seed)] = [q["srt_id"] for q in get_session_questions(main.questions_db, per_competency=3)]
    golden("pm_selection_seeded", picks)


def test_every_pm_form_is_three_per_competency_without_repeats():
    pool_before = copy.deepcopy(main.questions_db)
    random.seed(99)
    for _ in range(500):
        form = get_session_questions(main.questions_db, per_competency=3)
        assert len(form) == 30
        assert len({q["srt_id"] for q in form}) == 30
        assert Counter(q["primary_competency"] for q in form) == {c: 3 for c in REQUIRED_COMPETENCIES}
        assert [q["question_number"] for q in form] == list(range(1, 31))
        assert all(set(q) == QUESTION_KEYS for q in form)
    assert main.questions_db == pool_before, "selection must never mutate the shared bank"
