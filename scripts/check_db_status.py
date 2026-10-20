import os
import sys
sys.path.insert(0, ".")

os.environ["ALLOW_PRODUCTION"] = "1"

from backend.app.db.database import SessionLocal
from backend.app.db.models import DBIncident, DBOutboxItem, DBCloudEvent

db = SessionLocal()
try:
    total_incidents = db.query(DBIncident).count()
    open_incidents = db.query(DBIncident).filter(DBIncident.status == "OPEN").count()
    total_outbox = db.query(DBOutboxItem).count()
    pending_outbox = db.query(DBOutboxItem).filter(DBOutboxItem.status.in_(["PENDING", "RETRYING", "PROCESSING"])).count()
    total_events = db.query(DBCloudEvent).count()
    print(f"Total Incidents: {total_incidents}")
    print(f"Open Incidents: {open_incidents}")
    print(f"Total Outbox Items: {total_outbox}")
    print(f"Pending/Retrying Outbox Items: {pending_outbox}")
    print(f"Total Events: {total_events}")
finally:
    db.close()
