"""Catálogo canónico de países admitidos por el ranking.

El cliente puede elegir un país, pero nunca decide la región usada para
segmentar la clasificación. Este módulo mantiene esa decisión en el servidor.
"""

REGION_COUNTRY_CODES: dict[str, frozenset[str]] = {
    "Americas": frozenset(
        "AG AR BB BO BR BS BZ CA CL CO CR CU DM DO EC GD GT GY HN HT JM KN LC "
        "MX NI PA PE PY SR SV TT US UY VC VE".split()
    ),
    "Europe": frozenset(
        "AD AL AT BA BE BG BY CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI "
        "LT LU LV MC MD ME MK MT NL NO PL PT RO RS RU SE SI SK SM UA VA".split()
    ),
    "Asia": frozenset(
        "AE AF AM AZ BD BH BN BT CN GE ID IL IN IQ IR JO JP KG KH KP KR KW KZ LA "
        "LB LK MM MN MV MY NP OM PH PK PS QA SA SG SY TH TJ TL TM TR UZ VN YE".split()
    ),
    "Africa": frozenset(
        "AO BF BI BJ BW CD CF CG CI CM CV DJ DZ EG ER ET GA GH GM GN GQ GW KE KM "
        "LR LS LY MA MG ML MR MU MW MZ NA NE NG RW SC SD SL SN SO SS ST SZ TD TG "
        "TN TZ UG ZA ZM ZW".split()
    ),
    "Oceania": frozenset("AU FJ FM KI MH NR NZ PG PW SB TO TV VU WS".split()),
}

COUNTRY_REGION = {
    country_code: region
    for region, country_codes in REGION_COUNTRY_CODES.items()
    for country_code in country_codes
}


def canonical_country_region(country: str) -> tuple[str, str]:
    """Normaliza un ISO 3166-1 alpha-2 y devuelve su región canónica."""
    country_code = country.strip().upper()
    region = COUNTRY_REGION.get(country_code)
    if region is None:
        raise ValueError("country must be a supported ISO alpha-2 code")
    return country_code, region
