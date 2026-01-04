# -*- coding: utf-8 -*-
"""
Career Mode Gating Logic

El frontend detecta el modo por URL (career-mode), pero el backend NO confía
y decide por game_mode + inferencias. Este módulo centraliza esa lógica.

Modo por regiones NO persiste puntaje ni estadísticas.
Solo modo carrera persiste en las tablas career_user_stats, stage_best, stage_runs.
"""


def should_persist_score(
    game_mode: str | None,
    region_key: str | None,
    game_region: str | None
) -> bool:
    """
    Determina si el score debe persistirse en la base de datos.
    
    Reglas Finales:
    1. Si game_mode != "career" y game_mode != None → False
    2. Si game_mode == "career" → True
    3. Si game_mode falta (legacy): persistir SOLO si hay evidencia explícita de career
       en game_region (ej "career-mode", termina en /career o == career).
       NO inferir por default.
    """
    # Caso 1 & 2: game_mode explícito
    if game_mode:
        return game_mode.lower() == "career"
    
    # Caso 3: game_mode falta (legacy)
    if not game_region:
        return False
        
    game_region_lower = game_region.lower()
    # Evidencia explícita
    if "career-mode" in game_region_lower or game_region_lower.endswith("/career") or game_region_lower == "career":
        return True
    
    return False


def require_career_mode(game_mode: str | None) -> bool:
    """
    Verifica que game_mode sea explícitamente "career".
    Para endpoints nuevos que no aceptan inferencia.
    
    Args:
        game_mode: Modo de juego del request
    
    Returns:
        True solo si game_mode == "career"
    
    Raises:
        ValueError si game_mode no es "career"
    """
    if not game_mode or game_mode.lower() != "career":
        raise ValueError("game_mode must be 'career' for this endpoint")
    return True
