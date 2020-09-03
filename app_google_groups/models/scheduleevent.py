from typing import Dict, NamedTuple


class ScheduleEvent(NamedTuple):
    event_id: int
    action_id: str
    timestamp: int
    payload: any
    table_name = "ggroups_schedule"

    @classmethod
    def from_db(cls, db_data: Dict[str, any]) -> "ScheduleEvent":
        return cls(
            event_id=db_data["event_id"],
            action_id=db_data["action_id"],
            timestamp=db_data["timestamp"],
            payload=db_data["payload"],
        )
