# -*- coding: utf-8 -*-
from fastapi import HTTPException
from schemas.score import StageCompleteRequest
from utils.career_scoring import get_difficulty_config

def infer_total_flags(stage_data: StageCompleteRequest) -> int | None:
    """
    Intenta inferir el total de banderas de la etapa.
    """
    if stage_data.flags_total is not None:
        return stage_data.flags_total
    
    if stage_data.groups:
        try:
            total = sum(g.get("flags_count", 0) for g in stage_data.groups if isinstance(g, dict))
            if total > 0:
                return total
        except Exception:
            pass
            
    return None

def compute_max_stage_score(total_flags: int) -> int:
    """
    Calcula el puntaje máximo teórico por etapa:
    - 10 puntos por bandera
    - 5 puntos de ruta limpia (máximo)
    - 5 puntos bonus tiempo (máximo)
    """
    return (10 * total_flags) + 5 + 5

def validate_stage_score(stage_data: StageCompleteRequest, expected_codes: list[str]):
    """
    Valida la integridad de los datos recibidos.
    """
    # 1. Básicos
    if stage_data.score < 0:
        raise HTTPException(status_code=422, detail="Score cannot be negative")
    
    if stage_data.time_seconds < 0:
        raise HTTPException(status_code=422, detail="time_seconds cannot be negative")
        
    config = get_difficulty_config(stage_data.difficulty)
    if len(stage_data.answers) != config['flags_total']:
        raise HTTPException(status_code=422, detail=f"{stage_data.difficulty} requires {config['flags_total']} answers")

    codes = [answer.country_code.lower() for answer in stage_data.answers]
    if len(set(codes)) != len(codes):
        raise HTTPException(status_code=422, detail="country codes must be unique within a stage")
    if set(codes) != set(expected_codes):
        raise HTTPException(status_code=422, detail="answers do not match the server-issued attempt")
    expected = set(expected_codes)
    if any(selected not in expected for answer in stage_data.answers for selected in answer.selected_codes):
        raise HTTPException(status_code=422, detail="selected country does not belong to the server-issued attempt")

    return True


def authoritative_answers(stage_data: StageCompleteRequest) -> list[dict]:
    """Deriva el resultado desde la secuencia de selecciones, no desde flags del cliente."""
    result: list[dict] = []
    for answer in stage_data.answers:
        country_code = answer.country_code.lower()
        selected_codes = [code.lower() for code in answer.selected_codes]
        correct = bool(selected_codes and selected_codes[-1] == country_code)
        wrong_attempts = sum(1 for code in selected_codes if code != country_code)
        result.append({
            "country_code": country_code,
            "selected_codes": selected_codes,
            "correct": correct,
            "used_hint": answer.used_hint,
            "wrong_attempts": wrong_attempts,
        })
    return result
