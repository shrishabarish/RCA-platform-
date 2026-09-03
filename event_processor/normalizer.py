import json
from pathlib import Path
from typing import List, Union, Dict, Any
from backend.app.models.event import CloudEvent


def normalize_event(raw_data: Union[Dict[str, Any], CloudEvent]) -> CloudEvent:
    """
    Validates and normalizes a single raw dictionary or CloudEvent object.
    """
    if isinstance(raw_data, CloudEvent):
        return raw_data
    return CloudEvent(**raw_data)


def load_synthetic_events(filepath: Union[str, Path]) -> List[CloudEvent]:
    """
    Reads a JSON file of raw event dicts and validates/normalizes
    each into a CloudEvent. Invalid events are skipped and logged.
    """
    raw_path = Path(filepath)
    if not raw_path.exists():
        raise FileNotFoundError(f"Synthetic events file not found: {raw_path}")

    with raw_path.open("r", encoding="utf-8") as f:
        raw_events = json.load(f)

    normalized: List[CloudEvent] = []
    for raw in raw_events:
        try:
            event = normalize_event(raw)
            normalized.append(event)
        except Exception as e:
            print(f"Skipping invalid event {raw.get('event_id', 'unknown')}: {e}")

    return normalized