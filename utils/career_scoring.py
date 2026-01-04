# -*- coding: utf-8 -*-
from typing import TypedDict, List, Optional

class StageConfig(TypedDict):
    flags_total: int
    time_limit: int
    bonus_time_15: int  # threshold for +15
    bonus_time_10: int  # threshold for +10
    bonus_time_5:  int  # threshold for +5

# Mapping simple de stage_id a configuración
STAGE_CONFIGS: dict[str, StageConfig] = {
    "1": {"flags_total": 14, "time_limit": 115, "bonus_time_15": 58, "bonus_time_10": 29, "bonus_time_5": 12},
    "2": {"flags_total": 14, "time_limit": 115, "bonus_time_15": 58, "bonus_time_10": 29, "bonus_time_5": 12},
    "3": {"flags_total": 14, "time_limit": 115, "bonus_time_15": 58, "bonus_time_10": 29, "bonus_time_5": 12},
    "4": {"flags_total": 14, "time_limit": 115, "bonus_time_15": 58, "bonus_time_10": 29, "bonus_time_5": 12},
    "5": {"flags_total": 18, "time_limit": 140, "bonus_time_15": 70, "bonus_time_10": 35, "bonus_time_5": 14},
    "6": {"flags_total": 18, "time_limit": 140, "bonus_time_15": 70, "bonus_time_10": 35, "bonus_time_5": 14},
    "7": {"flags_total": 18, "time_limit": 140, "bonus_time_15": 70, "bonus_time_10": 35, "bonus_time_5": 14},
    "8": {"flags_total": 18, "time_limit": 140, "bonus_time_15": 70, "bonus_time_10": 35, "bonus_time_5": 14},
    "9": {"flags_total": 18, "time_limit": 140, "bonus_time_15": 70, "bonus_time_10": 35, "bonus_time_5": 14},
    "10": {"flags_total": 18, "time_limit": 140, "bonus_time_15": 70, "bonus_time_10": 35, "bonus_time_5": 14},
}

def get_stage_config(stage_id: str) -> Optional[StageConfig]:
    # Soporta tanto "1" como "stage_1"
    clean_id = stage_id.replace("stage_", "")
    return STAGE_CONFIGS.get(clean_id)

def compute_group_score(correct: int, had_errors: bool) -> int:
    """max(0, 10 * correct - (had_errors ? 5 : 0))"""
    penalty = 5 if had_errors and correct > 0 else 0
    return max(0, 10 * correct - penalty)

def compute_hint_bonus(hints_used: int) -> int:
    """(2 - hints_used) * 10 con clamp 0..20"""
    bonus = (2 - hints_used) * 10
    return max(0, min(20, bonus))

def compute_time_bonus(stage_id: str, time_seconds: int) -> int:
    """Calcula bonus de tiempo basado en tramos configurados."""
    config = get_stage_config(stage_id)
    if not config:
        return 0
    
    remaining = max(0, config["time_limit"] - time_seconds)
    
    if remaining >= config["bonus_time_15"]:
        return 15
    if remaining >= config["bonus_time_10"]:
        return 10
    if remaining >= config["bonus_time_5"]:
        return 5
    return 0

def compute_stage_score(stage_id: str, groups: list, hints_used: int, time_seconds: int) -> int:
    """Calcula el score total autoritativo del servidor."""
    # 1. Puntaje por grupos
    total_group_score = 0
    for g in groups:
        # El frontend manda 'correct' y 'had_errors'
        correct = g.get("correct", 0)
        had_errors = g.get("had_errors", False)
        total_group_score += compute_group_score(correct, had_errors)
    
    # 2. Bonus pistas
    hint_bonus = compute_hint_bonus(hints_used)
    
    # 3. Bonus tiempo
    time_bonus = compute_time_bonus(stage_id, time_seconds)
    
    return total_group_score + hint_bonus + time_bonus
