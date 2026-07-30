from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area='research_ingest',
    purpose='Kwarantanna research ingest: tworzenie, review, promocja lub odrzucenie.',
    min_profile='clean_operator',
    risk='medium',
    recommended_first_action='list_queue',
    actions=(
        WorkshopAction(
            action='create',
            tool_name='create_ingest_item',
            purpose='Dodaj materiaĹ‚ do kolejki ingest.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'raw_text': 'str', 'title': 'str|null', 'project_key': 'str|null'},
        ),
        WorkshopAction(
            action='list_queue',
            tool_name='list_ingest_queue',
            purpose='Lista ingest queue.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'project_key': 'str|null', 'limit': 'int'},
        ),
        WorkshopAction(
            action='get',
            tool_name='get_ingest_item',
            purpose='Pobierz item ingest.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'ingest_item_id': 'int'},
        ),
        WorkshopAction(
            action='promote',
            tool_name='promote_ingest_item',
            purpose='Promuj item do memory.',
            min_profile='clean_operator',
            risk='medium',
            payload_schema={'ingest_item_id': 'int', 'memory_content': 'str', 'memory_type': 'str'},
        ),
        WorkshopAction(
            action='reject',
            tool_name='reject_ingest_item',
            purpose='OdrzuÄ‡ item ingest.',
            min_profile='clean_operator',
            risk='medium',
            payload_schema={'ingest_item_id': 'int', 'reason': 'str'},
        ),
    ),
    guardrails=(),
)
