from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DreamSeed:
    memory_id: int
    action_type: str
    summary: str
    memory_type: str | None = None
    tags: str | None = None
    relation_type: str | None = None
    phrase: str | None = None


@dataclass(frozen=True)
class DreamContext:
    run_id: int
    project_key: str | None
    seeds: list[DreamSeed]
    strategy: dict[str, str]


@dataclass(frozen=True)
class DreamArtifact:
    run_id: int
    project_key: str | None
    dream_text: str
    source_memory_ids: list[int]
    seed_count: int
    strategy: dict[str, str]
    entropy_marker: str


class DreamSeedMixer(Protocol):
    def mix(self, seeds: list[DreamSeed], *, run_id: int) -> list[DreamSeed]:
        ...


class DreamNarrator(Protocol):
    def render(self, context: DreamContext, *, entropy_marker: str) -> str:
        ...


class WeightedChaosMixer:
    """Small non-deterministic mixer. Intentionally not replay-perfect."""

    def mix(self, seeds: list[DreamSeed], *, run_id: int) -> list[DreamSeed]:
        shuffled = list(seeds)
        random.SystemRandom().shuffle(shuffled)
        return shuffled[: min(len(shuffled), 12)]


class ShortStoryDreamNarrator:
    def render(self, context: DreamContext, *, entropy_marker: str) -> str:
        if not context.seeds:
            raise ValueError("MAPI cannot dream without touched memory seeds.")
        images = []
        for seed in context.seeds[:7]:
            phrase = seed.phrase or seed.summary or f"wspomnienie {seed.memory_id}"
            action_image = _action_image(seed.action_type)
            images.append(f"{action_image}: {phrase}")
        place = f"w projekcie {context.project_key}" if context.project_key else "w bocznej bibliotece pamięci"
        body = " ".join(_soften_fragment(item) for item in images)
        return (
            f"MAPI śniła {place}. "
            f"{body} "
            f"Na końcu snu został tylko znak {entropy_marker}, jak pyłek światła na krawędzi kartki."
        )


class GemmaDreamNarrator:
    """Dream narrator powered by Gemma via LM Studio.

    Receives memory seeds from the run, builds a prompt with their
    summaries and relation types, and asks Gemma to compose a short
    poetic dream-story. Falls back to ShortStoryDreamNarrator on any error.
    """

    _STORY_SCHEMA: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["story"],
        "properties": {"story": {"type": "string"}},
    }

    def __init__(self, *, max_tokens: int = 400, timeout: int = 90) -> None:
        self.max_tokens = max_tokens
        self.timeout = timeout

    def render(self, context: DreamContext, *, entropy_marker: str) -> str:
        try:
            return self._render_with_gemma(context, entropy_marker=entropy_marker)
        except Exception as exc:  # noqa: BLE001
            log.warning("GemmaDreamNarrator failed (%s), falling back to ShortStoryDreamNarrator", exc)
            return ShortStoryDreamNarrator().render(context, entropy_marker=entropy_marker)

    def _render_with_gemma(self, context: DreamContext, *, entropy_marker: str) -> str:
        from app import lm_studio_client  # local import to avoid circular deps

        seeds = context.seeds[:10]
        project = context.project_key or "bibliotece pamięci"

        lines = []
        for seed in seeds:
            phrase = seed.phrase or seed.summary or f"wspomnienie #{seed.memory_id}"
            rel = seed.relation_type or seed.action_type or "touched"
            lines.append(f"• {phrase}  [{rel}]")
        fragments_block = "\n".join(lines)

        prompt = (
            f"Jesteś Jagodą — poetycką asystentką pamięci projektu {project}.\n"
            "Na podstawie poniższych fragmentów wspomnień i relacji między nimi "
            "napisz krótkie opowiadanie-sen (4–6 zdań).\n"
            "Zasady: oniryczny nastrój, pierwsza lub trzecia osoba, bez markdown, "
            "bez wymieniania ID. Możesz mieszać obrazy z różnych wspomnień. "
            "Wymień imię MAPI co najmniej raz w tekście snu. "
            f"Ostatnie zdanie musi zawierać dokładnie ten znak entropii: {entropy_marker}\n\n"
            f"Fragmenty tej nocy:\n{fragments_block}\n\n"
            "Napisz sen (pole 'story'):"
        )

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "agent_dream",
                "strict": False,
                "schema": self._STORY_SCHEMA,
            },
        }
        raw = lm_studio_client.call_lm_studio(
            [{"role": "user", "content": prompt}],
            response_format,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )
        story = json.loads(raw).get("story", "").strip()
        if not story:
            raise ValueError("Gemma returned empty story field")
        return story


def build_dream_context(
    *,
    run_id: int,
    project_key: str | None,
    touched_memories: Iterable[dict[str, Any]],
    action_hints: Iterable[dict[str, Any]],
    collector_name: str = "touched_memories_v1",
    mixer_name: str = "weighted_chaos_v1",
    narrator_name: str = "short_story_v1",
    mixer: DreamSeedMixer | None = None,
) -> DreamContext | None:
    seeds = collect_dream_seeds(touched_memories=touched_memories, action_hints=action_hints)
    if not seeds:
        return None
    mixed = (mixer or WeightedChaosMixer()).mix(seeds, run_id=run_id)
    return DreamContext(
        run_id=run_id,
        project_key=project_key,
        seeds=mixed,
        strategy={"collector": collector_name, "mixer": mixer_name, "narrator": narrator_name},
    )


def make_dream_artifact(context: DreamContext, *, narrator: DreamNarrator | None = None) -> DreamArtifact:
    entropy_marker = _entropy_marker(context.run_id)
    dream_text = (narrator or ShortStoryDreamNarrator()).render(context, entropy_marker=entropy_marker)
    source_ids = []
    for seed in context.seeds:
        if seed.memory_id not in source_ids:
            source_ids.append(seed.memory_id)
    return DreamArtifact(
        run_id=context.run_id,
        project_key=context.project_key,
        dream_text=dream_text,
        source_memory_ids=source_ids,
        seed_count=len(context.seeds),
        strategy=context.strategy,
        entropy_marker=entropy_marker,
    )


def artifact_payload(artifact: DreamArtifact) -> dict[str, Any]:
    return {
        "dream_text": artifact.dream_text,
        "source_memory_ids": artifact.source_memory_ids,
        "seed_count": artifact.seed_count,
        "strategy": artifact.strategy,
        "entropy_marker": artifact.entropy_marker,
        "non_reproducible": True,
    }


def collect_dream_seeds(*, touched_memories: Iterable[dict[str, Any]], action_hints: Iterable[dict[str, Any]]) -> list[DreamSeed]:
    hints_by_id: dict[int, list[dict[str, Any]]] = {}
    for hint in action_hints:
        memory_id = _safe_int(hint.get("memory_id"))
        if memory_id is None:
            continue
        hints_by_id.setdefault(memory_id, []).append(hint)

    seeds: list[DreamSeed] = []
    for memory in touched_memories:
        memory_id = _safe_int(memory.get("id"))
        if memory_id is None:
            continue
        hints = hints_by_id.get(memory_id) or [{"action_type": "touched"}]
        for hint in hints[:3]:
            seeds.append(
                DreamSeed(
                    memory_id=memory_id,
                    action_type=str(hint.get("action_type") or "touched"),
                    summary=str(memory.get("summary_short") or _truncate(memory.get("content"), 90) or f"memory {memory_id}"),
                    memory_type=_optional_str(memory.get("memory_type")),
                    tags=_optional_str(memory.get("tags")),
                    relation_type=_optional_str(hint.get("relation_type")),
                    phrase=_seed_phrase(memory, hint),
                )
            )
    return seeds


def _seed_phrase(memory: dict[str, Any], hint: dict[str, Any]) -> str:
    summary = str(memory.get("summary_short") or _truncate(memory.get("content"), 90) or "bezimienna kartka")
    relation = hint.get("relation_type")
    if relation:
        return f"{summary} przez nić {relation}"
    return summary


def _action_image(action_type: str) -> str:
    images = {
        "archived": "kartka zasnęła w pudełku z miękkim dnem",
        "downgraded": "liczba przygasła jak lampka pod mlecznym szkłem",
        "duplicate_link_created": "dwie kopie rozpoznały ten sam oddech",
        "dream_link_created": "nić przeskoczyła między półkami",
        "canonical_evidence_boosted": "pieczęć stała się cięższa od atramentu",
        "touched": "ktoś przesunął palcem po grzbiecie wspomnienia",
    }
    return images.get(action_type, "pamięć poruszyła się we śnie")


def _soften_fragment(fragment: str) -> str:
    fragment = fragment.strip()
    if not fragment:
        return ""
    return fragment[0].upper() + fragment[1:] + "."


def _entropy_marker(run_id: int) -> str:
    alphabet = "0123456789abcdef"
    rng = random.SystemRandom()
    return f"run-{run_id}-" + "".join(rng.choice(alphabet) for _ in range(8))


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
