"""Versioned, deterministic rules for private Flag Races."""

from __future__ import annotations

import hashlib
import hmac
import random
from collections.abc import Iterable

from utils.career_rules import CURRENT_CONTENT_VERSION
from utils.country_regions import REGION_COUNTRY_CODES

RACE_RULESET_VERSION = 1
RACE_FLAGS_TOTAL = 12
RACE_DURATION_SECONDS = 90
RACE_COUNTDOWN_SECONDS = 3
RACE_WRONG_LOCK_SECONDS = 1.5
RACE_MIN_PLAYERS = 2
RACE_MAX_PLAYERS = 8

RACE_SCOPES = ("World", "Americas", "Europe", "Asia", "Africa", "Oceania")
RACE_DIFFICULTIES = ("easy", "normal", "hard")

TIER_COUNTS = {
    "easy": {"familiar": 7, "intermediate": 4, "expert": 1},
    "normal": {"familiar": 4, "intermediate": 5, "expert": 3},
    "hard": {"familiar": 2, "intermediate": 4, "expert": 6},
}

FAMILIAR_CODES = {
    "ar", "au", "at", "be", "br", "ca", "cl", "cn", "co", "cr", "cu", "dk",
    "eg", "fi", "fr", "de", "gr", "in", "ie", "it", "jp", "kr", "mx", "nl",
    "nz", "no", "pe", "pt", "za", "es", "se", "ch", "tr", "gb", "us", "uy",
    "ve", "ma", "ng", "th", "ph", "id", "sa", "ae", "il", "pk", "pl", "ua",
    "ru", "ke", "gh",
}
EXPERT_CODES = {
    "ad", "ag", "bb", "bh", "bi", "bn", "bw", "cv", "cy", "dj", "dm", "er",
    "fj", "fm", "ga", "gm", "gd", "gq", "gw", "ki", "km", "kn", "kw", "lc",
    "li", "ls", "lu", "lv", "mh", "ml", "mt", "mu", "mv", "na", "nr", "pw",
    "qa", "rw", "sb", "sc", "sl", "sm", "sn", "so", "sr", "st", "sz", "td",
    "tg", "to", "tv", "vu", "ws",
}


def recognition_tier(code: str) -> str:
    if code in FAMILIAR_CODES:
        return "familiar"
    if code in EXPERT_CODES:
        return "expert"
    return "intermediate"


def scope_codes(scope: str) -> list[str]:
    if scope == "World":
        return sorted({code.lower() for codes in REGION_COUNTRY_CODES.values() for code in codes})
    if scope not in REGION_COUNTRY_CODES:
        raise ValueError("Unsupported race scope")
    return sorted(code.lower() for code in REGION_COUNTRY_CODES[scope])


def _take(pool: list[str], count: int, rng: random.Random) -> list[str]:
    shuffled = list(pool)
    rng.shuffle(shuffled)
    return shuffled[:count]


def build_race_plan(scope: str, difficulty: str, seed: str) -> list[dict[str, object]]:
    """Build the shared country order and distractor set for one round.

    The stored plan is common to every participant. Only the visual order of
    each question's four options is personalized later.
    """
    if difficulty not in RACE_DIFFICULTIES:
        raise ValueError("Unsupported race difficulty")
    available = scope_codes(scope)
    if len(available) < RACE_FLAGS_TOTAL:
        raise ValueError("Race scope does not contain enough countries")

    rng = random.Random(seed)
    tiers = {
        tier: [code for code in available if recognition_tier(code) == tier]
        for tier in ("familiar", "intermediate", "expert")
    }
    selected: list[str] = []
    for tier in ("familiar", "intermediate", "expert"):
        wanted = TIER_COUNTS[difficulty][tier]
        selected.extend(_take([code for code in tiers[tier] if code not in selected], wanted, rng))

    # Small regions may not meet the exact curated mix. Fill only from the same
    # scope, preserving the promised route and total length.
    if len(selected) < RACE_FLAGS_TOTAL:
        selected.extend(_take([code for code in available if code not in selected], RACE_FLAGS_TOTAL - len(selected), rng))
    rng.shuffle(selected)

    plan: list[dict[str, object]] = []
    for answer in selected:
        distractors = _take([code for code in available if code != answer], 3, rng)
        plan.append({"country_code": answer, "option_codes": [answer, *distractors]})
    return plan


def participant_plan(plan: Iterable[dict[str, object]], round_id: str, user_id: int, secret: str) -> list[dict[str, object]]:
    """Return a stable A/B/C/D order that differs for each participant."""
    result: list[dict[str, object]] = []
    for index, question in enumerate(plan):
        options = list(question["option_codes"])
        digest = hmac.new(
            secret.encode("utf-8"),
            f"{round_id}:{user_id}:{index}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        random.Random(digest).shuffle(options)
        result.append({"country_code": question["country_code"], "option_codes": options})
    return result


def race_versions() -> dict[str, int]:
    return {"ruleset_version": RACE_RULESET_VERSION, "content_version": CURRENT_CONTENT_VERSION}
