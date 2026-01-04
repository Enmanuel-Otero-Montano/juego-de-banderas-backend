# -*- coding: utf-8 -*-
from fastapi import HTTPException
from schemas.score import StageCompleteRequest
from config import settings

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
    - 20 puntos bonus pistas (máximo)
    - 15 puntos bonus tiempo (máximo)
    """
    return (10 * total_flags) + 20 + 15

def validate_stage_score(stage_data: StageCompleteRequest):
    """
    Valida la integridad de los datos recibidos.
    """
    # 1. Básicos
    if stage_data.score < 0:
        raise HTTPException(status_code=422, detail="Score cannot be negative")
    
    if stage_data.time_seconds <= 0:
        raise HTTPException(status_code=422, detail="time_seconds must be positive")
        
    if not (0 <= stage_data.hints_used <= 2):
        raise HTTPException(status_code=422, detail="hints_used must be between 0 and 2")

    # 2. Validar grupos
    if not stage_data.groups or len(stage_data.groups) == 0:
        raise HTTPException(status_code=422, detail="groups cannot be empty")
    
    for i, g in enumerate(stage_data.groups):
        if not isinstance(g, dict):
            raise HTTPException(status_code=422, detail=f"Group {i} must be an object")
            
        flags_count = g.get("flags_count", 0)
        correct = g.get("correct", 0)
        
        if flags_count <= 0:
            raise HTTPException(status_code=422, detail=f"Group {i}: flags_count must be > 0")
            
        if correct < 0 or correct > flags_count:
            raise HTTPException(status_code=422, detail=f"Group {i}: correct ({correct}) must be between 0 and {flags_count}")

    return True
