from sqlalchemy import Column, String, Float, DateTime, JSON, ForeignKey, Integer, Index, Text, func, text
from sqlalchemy.orm import relationship, foreign
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

    # Truthful delivery tracking
    archive_status = Column(String, default="PENDING", index=True)
    notification_status = Column(String, default="PENDING", index=True)
    delivery_error = Column(Text, nullable=True)
    analysis_status = Column(String, default="PENDING", index=True)
    analyzed_at = Column(DateTime, nullable=True)

    # Relationships
    events = relationship("DBCloudEvent", back_populates="incident")
    outbox_items = relationship("DBOutboxItem", back_populates="incident", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_incidents_status_created_at", "status", "created_at"),
        Index("ix_incidents_open_created_at", "created_at", postgresql_where=text("status = 'OPEN'")),
    )


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

    # Optional relationship to an incident
    incident_id = Column(String, ForeignKey("incidents.incident_id"), nullable=True)
    incident = relationship("DBIncident", back_populates="events")

    # Ingestion timestamp for late/skewed event protection
    ingested_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_cloud_events_service_timestamp", "service", "timestamp"),
        Index("ix_cloud_events_unassigned", "timestamp", postgresql_where=text("incident_id IS NULL")),
    )


class DBOutboxItem(Base):
    __tablename__ = "outbox_events"

    id = Column(String, primary_key=True, index=True)
    incident_id = Column(String, ForeignKey("incidents.incident_id"), nullable=False, index=True)
    event_type = Column(String, nullable=False, default="SNS_NOTIFICATION")  # "S3_ARCHIVE" or "SNS_NOTIFICATION"
    payload_json = Column(JSON, nullable=False)
    status = Column(String, default="PENDING", index=True)  # PENDING, PROCESSING, RETRYING, SENT, FAILED, DEAD_LETTER
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=8)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime, nullable=True)
    lease_until = Column(DateTime, nullable=True, index=True)
    next_retry_at = Column(DateTime, nullable=True, index=True)

    incident = relationship("DBIncident", back_populates="outbox_items")


class DBSystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(String, nullable=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
