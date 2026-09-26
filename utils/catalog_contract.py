"""Instantánea comparable del catálogo que el ranking comparte con mobile."""

from utils.career_rules import (
    CURRENT_CONTENT_VERSION,
    CURRENT_RULESET_VERSION,
    CURRENT_SEASON_ID,
    STAGE_COUNTRY_CODES,
)
from utils.career_scoring import DIFFICULTY_CONFIGS
from utils.country_regions import REGION_COUNTRY_CODES

REGION_ORDER = ("Americas", "Europe", "Asia", "Africa", "Oceania")
DIFFICULTY_ORDER = ("easy", "normal", "hard")


def _assert_unique(codes: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for code in codes:
        if code in seen:
            duplicates.append(code)
        seen.add(code)
    if duplicates:
        raise ValueError(f"{label} repeats codes: {', '.join(sorted(set(duplicates)))}")


def build_catalog_contract() -> dict:
    if set(REGION_COUNTRY_CODES) != set(REGION_ORDER):
        raise ValueError(f"Regions must be exactly {', '.join(REGION_ORDER)}")
    if set(DIFFICULTY_CONFIGS) != set(DIFFICULTY_ORDER):
        raise ValueError(f"Difficulties must be exactly {', '.join(DIFFICULTY_ORDER)}")

    seen: set[str] = set()
    regions: dict[str, list[str]] = {}
    for region in REGION_ORDER:
        codes = sorted(code.lower() for code in REGION_COUNTRY_CODES[region])
        _assert_unique(codes, region)
        overlap = seen.intersection(codes)
        if overlap:
            raise ValueError(f"Country codes repeated across regions: {', '.join(sorted(overlap))}")
        seen.update(codes)
        regions[region] = codes

    stages = []
    for stage_id in sorted(STAGE_COUNTRY_CODES):
        codes = [code.lower() for code in STAGE_COUNTRY_CODES[stage_id]]
        _assert_unique(codes, f"Stage {stage_id}")
        missing = [code for code in codes if code not in seen]
        if missing:
            raise ValueError(f"Stage {stage_id} uses codes outside the catalog: {', '.join(missing)}")
        stages.append({"id": stage_id, "codes": codes})

    return {
        "seasonId": CURRENT_SEASON_ID,
        "rulesetVersion": CURRENT_RULESET_VERSION,
        "contentVersion": CURRENT_CONTENT_VERSION,
        "difficulties": {
            difficulty: {
                "flags": DIFFICULTY_CONFIGS[difficulty]["flags_total"],
                "seconds": DIFFICULTY_CONFIGS[difficulty]["time_limit"],
            }
            for difficulty in DIFFICULTY_ORDER
        },
        "regions": regions,
        "stages": stages,
    }
