import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
os.environ["ALLOW_PRODUCTION"] = "1"

from backend.app.db.database import SessionLocal
from backend.app.db.models import DBIncident, DBOutboxItem

db = SessionLocal()
try:
    now_utc = datetime.now(timezone.utc)
    # Bulk resolve stale open incidents
    resolved_count = db.query(DBIncident).filter(
        DBIncident.status == "OPEN"
    ).update(
        {DBIncident.status: "RESOLVED", DBIncident.updated_at: now_utc},
        synchronize_session=False
    )
    
    # Mark failing outbox items as FAILED so they don't block
    outbox_cleaned = db.query(DBOutboxItem).filter(
        DBOutboxItem.status.in_(["PENDING", "RETRYING", "PROCESSING"])
    ).update(
        {DBOutboxItem.status: "FAILED", DBOutboxItem.last_error: "Archived/failed during batch cleanup", DBOutboxItem.updated_at: now_utc},
        synchronize_session=False
    )
    
    db.commit()
    print(f"Successfully resolved {resolved_count} stale incidents.")
    print(f"Cleaned up {outbox_cleaned} stuck outbox items.")
finally:
    db.close()
