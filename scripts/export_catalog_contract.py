#!/usr/bin/env python3
"""Imprime el contrato de catálogo leído del código del backend."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.catalog_contract import build_catalog_contract


def main() -> None:
    json.dump(build_catalog_contract(), sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
