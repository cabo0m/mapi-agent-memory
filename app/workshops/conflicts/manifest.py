from __future__ import annotations

from app.workshops.contracts import Workshop, WorkshopAction

WORKSHOP = Workshop(
    area='conflicts',
    purpose='Raporty, klastry i decyzje dla sprzecznych memories.',
    min_profile='clean_operator',
    risk='medium',
    recommended_first_action='clusters',
    actions=(
        WorkshopAction(
            action='clusters',
            tool_name='get_conflict_clusters',
            purpose='PokaĹĽ klastry konfliktĂłw.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='registry',
            tool_name='get_conflict_registry',
            purpose='Jawny rejestr konfliktow ze statusem open/resolved/ignored.',
            min_profile='clean_operator',
            risk='low',
            payload_schema={'include_resolved': 'bool'},
        ),
        WorkshopAction(
            action='report',
            tool_name='get_conflict_report',
            purpose='Raport pary konfliktĂłw.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='history',
            tool_name='get_conflict_history',
            purpose='Historia konfliktĂłw dla memory.',
            min_profile='clean_operator',
            risk='low',
            payload_schema=None,
        ),
        WorkshopAction(
            action='preview_resolution',
            tool_name='preview_conflict_resolution',
            purpose='Preview rozstrzygniÄ™cia.',
            min_profile='clean_operator',
            risk='medium',
            payload_schema=None,
        ),
        WorkshopAction(
            action='record_decision',
            tool_name='record_conflict_decision',
            purpose='Zapisz decyzjÄ™ konfliktowÄ….',
            min_profile='clean_operator',
            risk='medium',
            payload_schema=None,
        ),
    ),
    guardrails=('Przy konflikcie pokazuj ĹşrĂłdĹ‚a i confidence przed wnioskiem.',),
)
