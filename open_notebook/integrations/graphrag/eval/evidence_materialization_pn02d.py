"""PN02D-B3P generation evidence-materialization treatment (provider-free core).

EVALUATION-ONLY. Nothing in production imports this. This module implements the ONE
causal variable the PN02D-B3O design isolates: FINAL-ANSWER EVIDENCE MATERIALIZATION.
It takes the arm's ALREADY-selected, ALREADY-member-filtered ``evidence_source_ids``
(produced by ``qastagepn02db2.B2QAStage._arm_evidence`` — arm selection is FROZEN) and
resolves each id to bounded source CONTENT, so the final-answer model can receive the
evidence text instead of Source ids only (the B3M/B3N ``G0`` gap).

Hard invariants (task B3P §5-§8/§14-§20/§38):
  * consumes EXACTLY the given ids — never adds / removes / re-ranks / discovers ids;
  * preserves the given order;
  * dereferences ONLY ids in the caller-supplied ``allowed_source_ids`` member set
    (defense-in-depth cross-notebook boundary — fail-closed on a non-member id);
  * fail-closed on a missing or empty selected source (never silently dropped);
  * deterministic per-source HEAD-PRESERVING (drop-tail) truncation — it keeps the
    FIRST ``MAX_CHARS_PER_SOURCE`` characters (``content[:N]``) and drops the tail,
    always retaining the source id;
  * caps the item count at the frozen arm cap.

Runtime/fixture separation (task B3P §11-§13): the RUNTIME resolver here reads the
isolated Surreal ``Source.full_text`` via the corpus ``record_id_by_key`` mapping and
the domain layer; it imports NO fixture module and never calls ``fx.source_text``. A
fixture/offline resolver (tests only) is built separately from ``fx.source_text`` and
is never imported by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Optional, Sequence, Tuple

#: Deterministic per-source content bound (task B3P §17). The frozen PN02 fixture's
#: sources are 69-123 chars, so this never truncates the fixture; it bounds prompt
#: growth for safety/generality.
MAX_CHARS_PER_SOURCE = 2000

#: Frozen worst-case evidence items per arm = notebook membership (8); QA-V <= 5,
#: QA-GD / QA-V+GD <= 8 (task B3P §18).
MAX_EVIDENCE_ITEMS_PER_ARM = 8


class EvidenceMaterializationError(RuntimeError):
    """Content-safe fail-closed materialization error (carries NO source text).

    Raised BEFORE any provider action so a missing/non-member/empty selected source
    can never silently degrade to a Source-id-only answer or leak cross-notebook.
    """


@dataclass(frozen=True)
class EvidenceItem:
    """One selected evidence source with its bounded content (task B3P §8).

    ``source_id`` is retained VERBATIM so the existing citation contract (the model
    cites Source ids) stays valid; ``content`` is the bounded, transient source text
    handed to the final-answer model — it is NEVER persisted to the B3 artifact/log.
    """

    source_id: str
    content: str


#: async (notebook_id, source_id) -> content | None. ``None`` means "no such source"
#: (fail-closed upstream). Injected: the runtime DB resolver in a live run, a fixture
#: resolver in offline tests.
EvidenceContentResolver = Callable[[str, str], Awaitable[Optional[str]]]

#: async (source_record_id) -> full_text | None. The runtime DB text fetcher edge
#: (mocked in tests; the domain-layer default below in a live run).
SourceTextFetcher = Callable[[str], Awaitable[Optional[str]]]

#: (record_id_by_key) -> EvidenceMaterializer. A POST-PROVISION factory: the per-run
#: ``ProvisionedCorpus.record_id_by_key`` mapping does not exist until corpus provisioning,
#: so the governed treatment path threads this factory (not a pre-built instance) and the
#: live driver calls it AFTER provisioning to construct the materializer (PN02D-B3P-R1 M1).
#: ``build_runtime_evidence_materializer`` matches this signature directly.
EvidenceMaterializerFactory = Callable[[Mapping[str, str]], "EvidenceMaterializer"]


@dataclass(frozen=True)
class EvidenceMaterializer:
    """Turns already-selected, member-filtered source ids into bounded ``EvidenceItem``s.

    ``materialize`` is the ONLY entry the QA stage calls in treatment mode. Control mode
    never constructs a materializer (default ``None``), so the baseline path is unchanged.
    """

    resolver: EvidenceContentResolver
    max_chars_per_source: int = MAX_CHARS_PER_SOURCE
    max_items_per_arm: int = MAX_EVIDENCE_ITEMS_PER_ARM

    async def materialize(
        self,
        notebook_id: str,
        source_ids: Sequence[str],
        *,
        allowed_source_ids: "frozenset[str]",
    ) -> Tuple[EvidenceItem, ...]:
        """Resolve ``source_ids`` (order-preserving) to bounded ``EvidenceItem``s.

        Fail-closed on: more items than the frozen arm cap, a non-member id
        (cross-notebook boundary), a missing source, or empty content. Never adds,
        removes, re-ranks, or dedups ids — a 1:1 order-preserving map.
        """
        ids = tuple(source_ids)
        if len(ids) > self.max_items_per_arm:
            raise EvidenceMaterializationError(
                f"selected evidence count {len(ids)} exceeds arm cap "
                f"{self.max_items_per_arm} (fail-closed)"
            )
        items: list[EvidenceItem] = []
        for source_id in ids:
            if source_id not in allowed_source_ids:
                # Defense-in-depth: arm evidence is already member-filtered, but a
                # non-member id must NEVER be dereferenced (cross-notebook hard gate).
                raise EvidenceMaterializationError(
                    f"selected source {source_id!r} is not a notebook member (fail-closed)"
                )
            content = await self.resolver(notebook_id, source_id)
            if content is None:
                raise EvidenceMaterializationError(
                    f"selected source {source_id!r} could not be resolved (fail-closed)"
                )
            if content == "":
                raise EvidenceMaterializationError(
                    f"selected source {source_id!r} has empty content (fail-closed)"
                )
            # Head-preserving (drop-tail) truncation: keep the FIRST max_chars_per_source
            # characters (content[:N]) and drop the tail; the source id is retained.
            items.append(
                EvidenceItem(
                    source_id=source_id,
                    content=content[: self.max_chars_per_source],
                )
            )
        return tuple(items)


def build_runtime_evidence_content_resolver(
    record_id_by_key: Mapping[str, str],
    source_text_fetcher: SourceTextFetcher,
) -> EvidenceContentResolver:
    """Build the RUNTIME (production-safe) resolver (task B3P §11).

    Maps a fixture-key evidence source id -> the isolated ``source`` record id via the
    corpus ``ProvisionedCorpus.record_id_by_key`` mapping, then reads the source text
    through the injected ``source_text_fetcher`` (the domain layer in a live run; a mock
    in tests). It imports NO fixture module and calls NO ``fx.source_text``. An id with no
    provisioned record fails closed (``None`` -> the materializer raises).
    """
    mapping = dict(record_id_by_key)

    async def _resolve(notebook_id: str, source_id: str) -> Optional[str]:
        record_id = mapping.get(source_id)
        if record_id is None:
            return None
        return await source_text_fetcher(record_id)

    return _resolve


async def default_source_text_fetcher(record_id: str) -> Optional[str]:
    """Runtime DB text fetcher: read ``Source.full_text`` via the polymorphic domain
    getter (``ObjectModel.get`` resolves by the ``source:`` id prefix). Runtime-safe: no
    fixture import, no provider call. Returns ``None`` when the record is absent."""
    from open_notebook.domain.notebook import Source

    source = await Source.get(record_id)
    if source is None:
        return None
    return getattr(source, "full_text", None)


def build_runtime_evidence_materializer(
    record_id_by_key: Mapping[str, str],
    *,
    source_text_fetcher: Optional[SourceTextFetcher] = None,
    max_chars_per_source: int = MAX_CHARS_PER_SOURCE,
    max_items_per_arm: int = MAX_EVIDENCE_ITEMS_PER_ARM,
) -> EvidenceMaterializer:
    """Compose the runtime materializer from a provisioned corpus mapping (task B3P §58).

    The caller (a FUTURE, separately-authorized controlled-ablation runner) builds this
    from the run's ``ProvisionedCorpus.record_id_by_key`` and injects it into the B2 QA
    stage. Provider-free at build time; the DB read happens only when the stage materializes.
    """
    fetcher = source_text_fetcher or default_source_text_fetcher
    resolver = build_runtime_evidence_content_resolver(record_id_by_key, fetcher)
    return EvidenceMaterializer(
        resolver=resolver,
        max_chars_per_source=max_chars_per_source,
        max_items_per_arm=max_items_per_arm,
    )


__all__ = [
    "MAX_CHARS_PER_SOURCE",
    "MAX_EVIDENCE_ITEMS_PER_ARM",
    "EvidenceMaterializationError",
    "EvidenceItem",
    "EvidenceContentResolver",
    "SourceTextFetcher",
    "EvidenceMaterializerFactory",
    "EvidenceMaterializer",
    "build_runtime_evidence_content_resolver",
    "default_source_text_fetcher",
    "build_runtime_evidence_materializer",
]
