import unittest
from schemas.score import ScoreRequest
from utils.score_validator import validate_score_legitimacy
from fastapi import HTTPException
from unittest.mock import MagicMock

class TestAntiCheat(unittest.TestCase):
    def test_valid_score(self):
        # Valid career score
        req = ScoreRequest(score=1500, game_mode="career", game_duration_seconds=300)
        self.assertTrue(validate_score_legitimacy(1500, req))

    def test_high_score_career(self):
        # > 2000 in career
        req = ScoreRequest(score=2500, game_mode="career", game_duration_seconds=300)
        with self.assertRaises(HTTPException) as cm:
            validate_score_legitimacy(2500, req)
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("Score too high", cm.exception.detail)

    def test_fast_completion_career(self):
        # 2000 points in 100 seconds (too fast, min 120 approx)
        req = ScoreRequest(score=1900, game_mode="career", game_duration_seconds=60)
        with self.assertRaises(HTTPException) as cm:
            validate_score_legitimacy(1900, req)
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("too short", cm.exception.detail)

    def test_impossible_speed(self):
        # Any mode, massive points in no time
        req = ScoreRequest(score=1000, game_duration_seconds=5)
        with self.assertRaises(HTTPException) as cm:
            validate_score_legitimacy(1000, req)
        self.assertEqual(cm.exception.status_code, 422)

    def test_no_metadata_legacy_compatibility(self):
        # Old frontend request (no metadata) should verify strictly on score? 
        # Current logic allows it if mode is not 'career'.
        # If mode not sent, we can't check mode limits.
        req = ScoreRequest(score=1000)
        self.assertTrue(validate_score_legitimacy(1000, req))

if __name__ == "__main__":
    unittest.main()
