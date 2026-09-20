# -*- coding: utf-8 -*-
"""Authoritative, player-readable scoring for career stages.

Every resolved flag is worth 10 points cleanly, 5 after a mistake, or 2 after
a hint. Time can add at most five points, so knowing flags always matters more
than tapping quickly.
"""
from typing import TypedDict


class DifficultyConfig(TypedDict):
    flags_total: int
    time_limit: int


DIFFICULTY_CONFIGS: dict[str, DifficultyConfig] = {
    'easy': {'flags_total': 8, 'time_limit': 100},
    'normal': {'flags_total': 10, 'time_limit': 95},
    'hard': {'flags_total': 12, 'time_limit': 90},
}


def get_difficulty_config(difficulty: str) -> DifficultyConfig:
    return DIFFICULTY_CONFIGS.get(difficulty, DIFFICULTY_CONFIGS['normal'])


def score_answer(answer: dict) -> int:
    if not answer.get('correct', False):
        return 0
    if answer.get('used_hint', False):
        return 2
    return 5 if int(answer.get('wrong_attempts', 0)) > 0 else 10


def calculate_score(answers: list[dict], time_seconds: int, difficulty: str) -> dict[str, int]:
    config = get_difficulty_config(difficulty)
    base_score = sum(score_answer(answer) for answer in answers)
    hints_used = sum(1 for answer in answers if answer.get('used_hint', False))
    mistakes = sum(int(answer.get('wrong_attempts', 0)) for answer in answers)
    remaining = max(0, config['time_limit'] - time_seconds)
    time_bonus = min(5, remaining // 15)
    clean_bonus = 5 if hints_used == 0 and mistakes == 0 else 0
    return {
        'score': base_score + time_bonus + clean_bonus,
        'base_score': base_score,
        'time_bonus': time_bonus,
        'clean_bonus': clean_bonus,
        'hints_used': hints_used,
        'mistakes': mistakes,
    }


def compute_stage_score(
    stage_id: str,
    groups: list[dict] | None,
    hints_used: int,
    time_seconds: int,
    answers: list[dict] | None = None,
    difficulty: str = 'normal',
) -> int:
    """Returns the score; legacy grouped payloads remain supported."""
    if answers:
        return calculate_score(answers, time_seconds, difficulty)['score']

    # Older web clients only report aggregate groups. Keep them operational
    # while new clients submit the per-flag data required by the new rules.
    total = 0
    for group in groups or []:
        correct = int(group.get('correct', 0))
        total += (5 if group.get('had_errors', False) else 10) * correct
    return total + min(5, max(0, get_difficulty_config(difficulty)['time_limit'] - time_seconds) // 15)
