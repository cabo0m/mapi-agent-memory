from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area='timeline',
    purpose='OĹ› projektu, historia decyzji i archiwum rozmĂłw.',
    min_profile='clean_operator',
    risk='low',
    recommended_first_action='search_verbatim',
    actions=(
        WorkshopAction(
            action='archive_conversation',
            tool_name='archive_conversation',
            purpose='Zarchiwizuj rozmowÄ™.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='get_conversation',
            tool_name='get_conversation',
            purpose='Pobierz zarchiwizowanÄ… rozmowÄ™.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='list_conversations',
            tool_name='list_conversations',
            purpose='Lista rozmĂłw bez peĹ‚nej treĹ›ci.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='search_verbatim',
            tool_name='search_verbatim',
            purpose='Wyszukiwanie verbatim w pamiÄ™ci i archiwum.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='reconstruct_day',
            tool_name='reconstruct_day',
            purpose='Reconstruct one local calendar day from durable first-party MAPI evidence.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={
                'date': 'str',
                'timezone': 'str',
                'project_key': 'str|null',
                'limit': 'int',
                'include_content': 'bool',
            },
        ),
    ),
    guardrails=(),
)
