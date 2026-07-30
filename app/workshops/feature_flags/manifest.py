from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area='feature_flags',
    purpose='PrzeglÄ…d flag funkcji i rolloutĂłw.',
    min_profile='clean_operator',
    risk='medium',
    recommended_first_action='list',
    actions=(
        WorkshopAction(
            action='list',
            tool_name='list_feature_flags',
            purpose='Lista flag funkcji.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='get',
            tool_name='get_feature_flag',
            purpose='Pobierz jednÄ… flagÄ™.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'flag_key': 'str'},
        ),
        WorkshopAction(
            action='evaluate',
            tool_name='evaluate_feature_flag',
            purpose='SprawdĹş ewaluacjÄ™ flagi.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'flag_key': 'str', 'project_key': 'str|null', 'scope_code': 'str|null'},
        ),
    ),
    guardrails=(),
)
