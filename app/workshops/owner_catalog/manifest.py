from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area='owner_catalog',
    purpose='Katalog wĹ‚aĹ›cicieli, mapowania rĂłl i rollout owner governance.',
    min_profile='clean_operator',
    risk='medium',
    recommended_first_action='health',
    actions=(
        WorkshopAction(
            action='health',
            tool_name='get_owner_catalog_health',
            purpose='Stan katalogu wĹ‚aĹ›cicieli.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'project_key': 'str|null'},
        ),
        WorkshopAction(
            action='list_items',
            tool_name='list_owner_directory_items',
            purpose='Lista owner targets.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'owner_type': 'str|null', 'active_only': 'bool'},
        ),
        WorkshopAction(
            action='list_mappings',
            tool_name='list_owner_role_mappings',
            purpose='Lista mapowaĹ„ owner_role -> owner_key.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'project_key': 'str|null', 'active_only': 'bool'},
        ),
        WorkshopAction(
            action='repair_summary',
            tool_name='get_owner_catalog_repair_summary',
            purpose='Podsumowanie napraw owner catalog.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'project_key': 'str|null'},
        ),
    ),
    guardrails=(),
)
