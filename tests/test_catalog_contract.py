"""El catálogo del backend debe coincidir con el artefacto compartido con mobile.

Ese JSON se regenera solo cuando ambos lados ya coinciden:

    node scripts/check-catalog-contract.mjs --backend /ruta/al/backend --write

El comando vive en el repo mobile (mobile/scripts). No editar el JSON a mano.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.catalog_contract import build_catalog_contract

CONTRACT_PATH = ROOT / "contracts" / "catalog-contract.json"


def test_backend_matches_committed_catalog_contract():
    committed = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    actual = build_catalog_contract()
    assert actual == committed, _diff(committed, actual)


def _diff(committed: dict, actual: dict) -> str:
    lines = ["El catálogo del backend no coincide con contracts/catalog-contract.json."]
    if committed.get("seasonId") != actual.get("seasonId"):
        lines.append(f"seasonId committed={committed.get('seasonId')!r} actual={actual.get('seasonId')!r}")
    if committed.get("rulesetVersion") != actual.get("rulesetVersion"):
        lines.append(f"rulesetVersion committed={committed.get('rulesetVersion')!r} actual={actual.get('rulesetVersion')!r}")
    if committed.get("contentVersion") != actual.get("contentVersion"):
        lines.append(f"contentVersion committed={committed.get('contentVersion')!r} actual={actual.get('contentVersion')!r}")
    committed_stages = [(stage["id"], stage["codes"]) for stage in committed.get("stages", [])]
    actual_stages = [(stage["id"], stage["codes"]) for stage in actual.get("stages", [])]
    if committed_stages != actual_stages:
        lines.append(f"stages committed={len(committed_stages)} actual={len(actual_stages)}")
    for region in ("Americas", "Europe", "Asia", "Africa", "Oceania"):
        left = set(committed.get("regions", {}).get(region, []))
        right = set(actual.get("regions", {}).get(region, []))
        if left != right:
            lines.append(
                f"{region} only in contract={sorted(left - right)} only in source={sorted(right - left)}"
            )
    if committed.get("difficulties") != actual.get("difficulties"):
        lines.append(f"difficulties committed={committed.get('difficulties')!r} actual={actual.get('difficulties')!r}")
    return "\n".join(lines)


if __name__ == "__main__":
    test_backend_matches_committed_catalog_contract()
    print("catalog contract ok")
