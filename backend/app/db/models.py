from sqlalchemy import Column, String, Float, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from backend.app.db.database import Base

class DBIncident(Base):
    __tablename__ = "incidents"

    incident_id = Column(String, primary_key=True, index=True)
    status = Column(String, index=True)
    severity = Column(String, index=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    probable_root_cause = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    affected_services = Column(JSON, default=[])
    score_breakdown = Column(JSON, default={})
    remediation_suggestion = Column(String, nullable=True)

    # Relationships
    events = relationship("DBCloudEvent", back_populates="incident")


class DBCloudEvent(Base):
    __tablename__ = "cloud_events"

    event_id = Column(String, primary_key=True, index=True)
    timestamp = Column(DateTime, index=True)
    source = Column(String)
    event_type = Column(String)
    service = Column(String, index=True)
    resource_id = Column(String)
    metric = Column(String, nullable=True)
    value = Column(Float, nullable=True)
    severity = Column(String)
    metadata_json = Column(JSON, default={})

    # Optional relationship to an incident (an event might not be correlated yet)
    incident_id = Column(String, ForeignKey("incidents.incident_id"), nullable=True)
    incident = relationship("DBIncident", back_populates="events")
