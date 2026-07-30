from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area='memory_linking',
    purpose='Deterministyczny pass linkowania pamiÄ™ci.',
    min_profile='clean_operator',
    risk='medium',
    recommended_first_action='preview',
    actions=(
        WorkshopAction(
            action='preview',
            tool_name='preview_memory_linking_pass',
            purpose='Preview linkowania pamiÄ™ci.',
            min_profile='clean_operator',
            risk='medium',
            payload_schema={'project_key': 'str|null', 'limit': 'int', 'min_score': 'float'},
        ),
        WorkshopAction(
            action='run',
            tool_name='run_memory_linking_pass',
            purpose='Uruchom linkowanie pamiÄ™ci.',
            min_profile='clean_operator',
            risk='medium',
            payload_schema={'project_key': 'str|null', 'limit': 'int', 'min_score': 'float'},
        ),
    ),
    guardrails=('Najpierw preview_memory_linking_pass, potem run_memory_linking_pass.',),
)
