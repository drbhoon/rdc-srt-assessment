"""Plant Manager arithmetic the app owns: English factor, ceiling, readiness tiers."""
import main
import report_generator
import scorer


def test_pm_scoring_settings_are_unchanged():
    assert main.ENGLISH_WEIGHT == 0.15
    assert main.SCORE_CEILING == 9 and scorer.SCORE_CEILING == 9
    # 75 -> 90 for Plant Manager and PQI alike, HR decision 2026-09-26. The
    # only Plant Manager setting that moved; scoring settings below are unchanged.
    assert main.ASSESSMENT_MINUTES == 90
    assert main.WATCHDOG_TIMEOUT_MINUTES == 15
    assert main._PIPELINE_MAX_CONCURRENT == 2
    assert main.READINESS_TIERS == [
        ("Ready for Higher Responsibility", 251, 6.5),
        ("Ready to be Plant Manager",       210, 6.0),
        ("Ready with Structured Support",   150, 5.0),
        ("Not Yet Ready",                    90, 0.0),
        ("Low Potential",                     0, 0.0),
    ]
    assert (scorer.REVIEW_LOW_THRESHOLD, scorer.REVIEW_HIGH_THRESHOLD) == (100, 250)
    assert scorer.REVIEW_MAX_TOKENS == 3000
    assert (scorer.SCORER_MODEL, scorer.SCORER_MAX_TOKENS) == ("claude-haiku-4-5", 2048)
    assert (scorer.MAX_ATTEMPTS, scorer.BACKOFF_SECONDS) == (3, (1, 2, 4))
    assert (report_generator.REPORT_MODEL, report_generator.REPORT_MAX_TOKENS) == ("claude-haiku-4-5", 16000)
    assert (report_generator.REPORT_MAX_ATTEMPTS, report_generator.REPORT_BACKOFF_SECONDS) == (3, (2, 5))


def test_english_adjustment_examples():
    adjust = main._adjust_for_english
    assert adjust(8, 1.0) == 8.0
    assert adjust(8, None) == 8.0          # legacy / missing factor: no adjustment
    assert adjust(8, 0.5) == 7.4           # 8 × (0.85 + 0.15 × 0.5)
    assert adjust(10, 1.0) == 9.0          # the 9 ceiling
    assert adjust(5, "garbage") == 5.0
    assert adjust(5, 7) == 5.0             # factor clamped to 1
    assert adjust(-3, 1.0) == 0.0


def test_english_adjustment_grid(golden):
    grid = {}
    for base in (0, 1, 2, 3, 4, 4.5, 5, 6, 7, 8, 9, 10):
        for factor in (None, 0, 0.25, 0.5, 0.55, 0.8, 1, 1.5, -1, "x"):
            grid[f"{base}|{factor}"] = main._adjust_for_english(base, factor)
    golden("pm_english_adjustment_grid", grid)


def _flat(score):
    return {f"C{i}": score for i in range(10)}


def test_readiness_examples():
    tier = main._compute_readiness_tier
    assert tier(251, _flat(6.5))["tier"] == "Ready for Higher Responsibility"
    assert tier(250.9, _flat(9.0))["tier"] == "Ready to be Plant Manager"

    demoted = tier(260, {**_flat(9.0), "C3": 6.4})
    assert demoted["tier"] == "Ready to be Plant Manager"
    assert demoted["demoted_from"] == "Ready for Higher Responsibility"
    assert demoted["weakest_competency"] == "C3"

    floor = tier(240, {**_flat(8.0), "C5": 4.9})
    assert floor["tier"] == "Not Yet Ready"
    assert floor["demoted_from"] == "Ready to be Plant Manager"

    assert tier(89.9, _flat(3.0))["tier"] == "Low Potential"
    assert tier(0, {})["tier"] == "Low Potential"

    capped = tier(260, _flat(7.0), integrity_override=True, integrity_reason="kept quiet")
    assert capped["tier"] == "Ready with Structured Support"
    assert capped["demoted_from"] == "Ready for Higher Responsibility"
    assert capped["reason"].startswith("INTEGRITY OVERRIDE") and capped["reason"].endswith(": kept quiet")

    # The integrity cap never raises a lower tier.
    assert tier(120, _flat(4.0), integrity_override=True)["tier"] == "Not Yet Ready"


def test_readiness_grid(golden):
    grid = {}
    for total in (0, 89.9, 90, 149.9, 150, 209.9, 210, 250, 250.9, 251, 270):
        for weakest in (0.0, 4.9, 5.0, 5.9, 6.0, 6.4, 6.5, 9.0):
            summary = {**_flat(9.0), "C7": weakest}
            for integrity in (False, True):
                grid[f"{total}|{weakest}|{integrity}"] = main._compute_readiness_tier(
                    total, summary, integrity_override=integrity, integrity_reason="evidence" if integrity else "",
                )
    golden("pm_readiness_grid", grid)
