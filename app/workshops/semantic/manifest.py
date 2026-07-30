from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area='semantic',
    purpose='Wyszukiwanie semantyczne i stan embeddingĂłw.',
    min_profile='clean_operator',
    risk='low',
    recommended_first_action='search',
    actions=(
        WorkshopAction(
            action='search',
            tool_name='search_semantic',
            purpose='Wyszukaj semantycznie.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'query': 'str', 'project_key': 'str|null', 'top_k': 'int'},
        ),
        WorkshopAction(
            action='stats',
            tool_name='get_semantic_embedding_stats',
            purpose='Statystyki embeddingĂłw.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
    ),
    guardrails=(),
)
