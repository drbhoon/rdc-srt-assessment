"""Balanced 30-SRT forms for PQI: exactly 3 per competency, stratified, never plain random.

Within each competency every possible trio of SRTs is enumerated (10 choose 3
is 120), and only the best-balanced trios are kept: first those with at least
one Decision_Applicable SRT, then those covering the most distinct Demand_Types.
A form takes one of those trios per competency, chosen uniformly, and is kept
only if its count of AFI=Yes SRTs falls in the master's target (18–22 for
1.0-PILOT); otherwise the whole form is redrawn. Rejection sampling keeps every
acceptable form equally likely, so balancing does not make forms predictable.

If the target cannot be met the form closest to it at or above the master's
safeguard floor is used, and the form records that it missed the target.

The seed is stored with the attempt, so any form can be reproduced exactly.
"""
from __future__ import annotations

import itertools
import random
import secrets
from collections import defaultdict


class PqiGenerationError(Exception):
    pass


def best_triples(srts: list) -> list:
    best, kept = None, []
    for trio in itertools.combinations(srts, 3):
        rank = (
            any(s["decision_applicable"] == "Yes" for s in trio),
            len({s["demand_type"] for s in trio}),
        )
        if best is None or rank > best:
            best, kept = rank, [trio]
        elif rank == best:
            kept.append(trio)
    return kept


def _pools(master) -> list:
    by_competency = defaultdict(list)
    for srt in master["srts"]:
        by_competency[srt["primary_competency"]].append(srt)
    pools = []
    for competency in master["competencies"]:
        trios = best_triples(by_competency[competency["code"]])
        if not trios:
            raise PqiGenerationError(f"{competency['code']} has fewer than 3 SRTs")
        pools.append((competency["code"], trios))
    return pools


def _afi_yes(srts) -> int:
    return sum(1 for s in srts if s["afi_applicability"] == "Yes")


def feasibility(master) -> tuple[int, int]:
    """The fewest and most AFI=Yes SRTs a balanced form can contain."""
    low = high = 0
    for _, trios in _pools(master):
        counts = [_afi_yes(t) for t in trios]
        low += min(counts)
        high += max(counts)
    return low, high


def generate_form(master, seed: int | None = None, max_attempts: int = 20000) -> dict:
    seed = secrets.randbits(63) if seed is None else int(seed)
    rng = random.Random(seed)
    target_low, target_high = master["afi_yes_target"]
    floor = master["afi_yes_floor"]
    pools = _pools(master)

    fallback = None
    chosen, afi, attempts = None, None, 0
    for attempts in range(1, max_attempts + 1):
        candidate = [srt for _, trios in pools for srt in rng.choice(trios)]
        count = _afi_yes(candidate)
        if target_low <= count <= target_high:
            chosen, afi = candidate, count
            break
        if count >= floor:
            distance = min(abs(count - target_low), abs(count - target_high))
            if fallback is None or distance < fallback[0]:
                fallback = (distance, candidate, count)
    within_target = chosen is not None
    if not within_target:
        if fallback is None:
            raise PqiGenerationError(f"no balanced form reached the AFI=Yes floor of {floor}")
        _, chosen, afi = fallback

    ordered = list(chosen)
    rng.shuffle(ordered)
    return {
        "srt_ids":        [s["srt_id"] for s in ordered],
        "seed":           seed,
        "attempts":       attempts,
        "afi_yes":        afi,
        "within_target":  within_target,
        "decision_items": sum(1 for s in ordered if s["decision_applicable"] == "Yes"),
    }
