from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from dependencies import get_db
from utils.limiter import limiter
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/health",
    tags=["health"]
)

@router.get("/live", status_code=status.HTTP_200_OK)
@limiter.exempt
async def health_live(request: Request):
    """
    Liveness check to ensure the process is running.
    """
    return {"status": "ok"}

@router.get("/ready", status_code=status.HTTP_200_OK)
@limiter.exempt
async def health_ready(request: Request, db: Session = Depends(get_db)):
    """
    Readiness check to ensure the database is accessible.
    """
    try:
        # Execute a simple SELECT 1 with a timeout (1 second = 1000ms)
        # Using statement_timeout for PostgreSQL via execution_options
        db.execute(
            text("SELECT 1"),
            execution_options={"timeout": 1, "statement_timeout": 1000}
        ).scalar()
        return {"status": "ok"}
    except Exception as e:
        # Internal log only, don't expose details to the user
        logger.error(f"Health check failed: Database is down or unreachable. Error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "fail", "db": "down"}
        )
