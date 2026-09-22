"""Contrato congelado del ranking de carrera.

Cambiar puntuación, tiempos, cantidad de preguntas o contenido requiere una
nueva versión/temporada; nunca se reinterpreta una marca histórica.
"""

CURRENT_SEASON_ID = "season-1"
CURRENT_RULESET_VERSION = 3
CURRENT_CONTENT_VERSION = 1
MIN_PASS_RATIO = 1.0
ATTEMPT_TTL_SECONDS = 180

RANKED_STAGE_IDS = frozenset(range(1, 13))

STAGE_COUNTRY_CODES: dict[int, tuple[str, ...]] = {
    1: ('ar','br','uy','cl','pe','bo','py','co','ve','ec','gy','sr'),
    2: ('us','ca','mx','cu','jm','pa','cr','do','gt','bs','ni','ht'),
    3: ('es','pt','fr','de','it','gb','ie','nl','be','ch','lu','li'),
    4: ('no','se','fi','dk','is','pl','cz','at','gr','ua','ee','lv'),
    5: ('cn','jp','kr','kp','in','pk','bd','id','ph','vn','kz','mn'),
    6: ('th','my','sg','kh','la','mm','np','bt','lk','mv','bn','tl'),
    7: ('sa','ae','qa','il','jo','lb','iq','ir','tr','cy','om','kw'),
    8: ('eg','ma','dz','tn','ly','za','ng','gh','ke','et','gm','sl'),
    9: ('sn','ci','cm','ug','tz','rw','sd','ss','so','mg','bi','er'),
    10: ('ao','mz','zm','zw','na','bw','cd','cg','ga','mu','mw','ls'),
    11: ('au','nz','fj','pg','sb','ws','to','vu','ki','fm','tv','mh'),
    12: ('hn','sv','ro','hu','ml','ne','pw','nr','by','tg','al','sk'),
}


def validate_stage_identity(route_position: int, content_stage_id: int) -> None:
    if route_position not in RANKED_STAGE_IDS or content_stage_id not in RANKED_STAGE_IDS:
        raise ValueError("ranked stages must be between 1 and 12")
    if route_position == 12 and content_stage_id != 12:
        raise ValueError("route position 12 must use the final content stage")
    if route_position < 12 and content_stage_id == 12:
        raise ValueError("the final content stage can only be played at route position 12")


def validate_country_codes(content_stage_id: int, country_codes: list[str], player_country: str | None) -> list[str]:
    normalized = [code.strip().lower() for code in country_codes]
    if any(len(code) != 2 or not code.isalpha() for code in normalized):
        raise ValueError("country codes must use two letters")
    if len(set(normalized)) != len(normalized):
        raise ValueError("country codes must be unique within an attempt")

    canonical = set(STAGE_COUNTRY_CODES[content_stage_id])
    player_code = player_country.lower() if player_country else None
    all_ranked_codes = set().union(*map(set, STAGE_COUNTRY_CODES.values()))
    outside = set(normalized) - canonical
    allowed_outside: set[str] = set()
    if player_code and player_code not in canonical:
        allowed_outside.add(player_code)
    elif player_code and player_code in canonical and player_code not in normalized:
        # Si el origen ya pertenecía a otro bloque, el cliente intercambia
        # esa bandera por la desplazada del bloque de origen.
        allowed_outside.update(all_ranked_codes)
    if not outside.issubset(allowed_outside):
        raise ValueError("country codes do not belong to the selected content stage")

    # La personalización puede sustituir como máximo una bandera por la del
    # país de origen. Impide construir una etapa arbitraria más fácil.
    missing = canonical - set(normalized)
    if len(outside) > 1 or (outside and len(missing) < 1):
        raise ValueError("invalid personalized stage composition")
    return normalized
