from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.metrics import metrics
from app.db.session import get_db
from app.models.application import Application
from app.services import storage

router = APIRouter(tags=["ops"])


@router.get("/metrics")
def read_metrics(db: Session = Depends(get_db)):
    """JSON telemetry for the ops floor.

    Provider latency/error counts are process-local. Application counts come from
    the database. This endpoint is unauthenticated like the rest of the prototype
    agent API, so do not expose it publicly.
    """
    rows = db.execute(select(Application.status, func.count(Application.id)).group_by(Application.status)).all()
    snapshot = metrics.snapshot()
    snapshot["applications"] = {status: count for status, count in rows}
    snapshot["storage_bytes"] = storage.usage_bytes()
    return snapshot
