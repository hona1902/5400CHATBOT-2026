"""Isolated PN02 vector-corpus provisioner for the LIVE driver (PN02D-B0C-B).

EVALUATION-ONLY. Nothing in production imports this (``PRODUCTION_IMPORTS_EVAL =
NO``). Approach B (design §13/§30-§33) needs ``source_embedding`` rows to exist before
the vector phase; those rows come from Open Notebook ``Source`` vectorization, NOT from
LightRAG graph indexing. This module prepares that isolated eval corpus:

  * resolve the frozen 21 canonical Sources + 24 ``reference`` membership edges;
  * provision ``source_embedding`` rows by vectorizing each canonical Source ONCE;
  * keep all state run-owned / test-owned (an isolated Surreal namespace in a real run;
    a fake DB in B0C-B tests) — the normal DB is never mutated
    (``NORMAL_DB_MUTATIONS = 0``, design §34);
  * support deterministic teardown.

**Corpus embedding workload (design §32).** Approach B keeps ONE canonical corpus and
scopes retrieval by membership, so each canonical Source is embedded exactly ONCE per
isolated corpus — NOT once per workspace copy. The count is therefore
``len(fixture.source_keys)`` = 21 (the canonical Source count), mechanically derived,
deterministic, and test-locked — distinct from the 24 workspace memberships and from
the 26 query embeddings.

**Corpus budget (design §33).** A dedicated ``CorpusEmbeddingBudget`` bounds corpus
vectorization; it is checked BEFORE each embedding and is SEPARATE from the driver's
``StatefulBudgetGuard`` (whose ``QUERY_EMBEDDING`` cap of 26 is never charged for corpus
work — ``CORPUS_EMBEDDING_ACCOUNTING_IMPLEMENTED = YES``).

Every external boundary (create Source, link ``reference``, vectorize) is INJECTED, so
B0C-B tests run against a fake DB + fake embedder with zero provider/DB traffic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Mapping

from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    require_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import FixturePN02

#: (fixture_key, title, text) -> isolated ``source`` record id.
SourceCreator = Callable[[str, str, str], Awaitable[str]]
#: (source_record_id, notebook_record_id) -> None (creates one ``reference`` edge).
ReferenceLinker = Callable[[str, str], Awaitable[None]]
#: source_record_id -> chunk count (populates ``source_embedding`` rows in-process).
SourceEmbedder = Callable[[str], Awaitable[int]]


class CorpusBudgetExceeded(RuntimeError):
    """A corpus source-embedding operation would exceed the dedicated corpus cap."""


class CorpusProvisioningError(RuntimeError):
    """The isolated corpus could not be provisioned (fail-closed, content-free)."""


@dataclass(frozen=True)
class CorpusWorkload:
    """The mechanically-derived, test-locked corpus provisioning workload (design §32)."""

    canonical_source_count: int
    reference_edge_count: int
    planned_source_embedding_operations: int
    max_source_embedding_operations: int
    #: Approach B embeds each canonical Source ONCE per isolated corpus (not per copy).
    embedded_once_per_isolated_corpus: bool = True

    def as_dict(self) -> Dict[str, object]:
        return {
            "canonical_source_count": self.canonical_source_count,
            "reference_edge_count": self.reference_edge_count,
            "planned_source_embedding_operations": (
                self.planned_source_embedding_operations
            ),
            "max_source_embedding_operations": self.max_source_embedding_operations,
            "embedded_once_per_isolated_corpus": self.embedded_once_per_isolated_corpus,
            "scheme": "APPROACH_B_SINGLE_CANONICAL_CORPUS",
        }


def derive_corpus_workload(fx: FixturePN02) -> CorpusWorkload:
    """Derive the corpus workload from the fixture — one embedding per canonical Source.

    Approach B stores one canonical corpus (not per-workspace copies), so the planned
    source-embedding operations equal the canonical Source count (21), NOT the 24
    workspace memberships. Deterministic and test-locked.
    """
    canonical = len(fx.source_keys)
    edges = len(fx.memberships)
    return CorpusWorkload(
        canonical_source_count=canonical,
        reference_edge_count=edges,
        planned_source_embedding_operations=canonical,
        max_source_embedding_operations=canonical,
    )


@dataclass
class CorpusEmbeddingBudget:
    """Dedicated bounded counter for corpus source vectorization (design §33).

    Checked BEFORE each embedding; SEPARATE from the driver's 26 query-embedding cap.
    """

    cap: int
    _spent: int = 0

    def reserve(self, n: int = 1) -> None:
        if self._spent + n > self.cap:
            raise CorpusBudgetExceeded(
                f"corpus source-embedding budget exceeded (cap={self.cap}, "
                f"spent={self._spent}, requested={n})"
            )
        self._spent += n

    @property
    def spent(self) -> int:
        return self._spent

    def snapshot(self) -> Dict[str, int]:
        return {"spent": self._spent, "cap": self.cap}


@dataclass(frozen=True)
class ProvisionedCorpus:
    """Content-safe result of provisioning the isolated corpus (design §30)."""

    run_id: str
    record_id_by_key: Mapping[str, str]
    source_embedding_operations: int
    reference_edges: int
    workload: CorpusWorkload
    budget_snapshot: Mapping[str, int]

    def member_id_resolver(self) -> Callable[[str], str]:
        """A fixture-key -> ``source`` record-id resolver for the vector backend."""
        mapping = dict(self.record_id_by_key)

        def _resolve(key: str) -> str:
            try:
                return mapping[key]
            except KeyError as exc:  # pragma: no cover - guarded upstream
                raise CorpusProvisioningError(
                    f"no provisioned corpus record for source key {key!r}"
                ) from exc

        return _resolve

    def as_public_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "canonical_sources_provisioned": len(self.record_id_by_key),
            "source_embedding_operations": self.source_embedding_operations,
            "reference_edges": self.reference_edges,
            "workload": self.workload.as_dict(),
            "corpus_embedding_budget": dict(self.budget_snapshot),
        }


class RealPN02CorpusProvisioner:
    """Provision the isolated eval vector corpus (Approach B, design §30-§33).

    Composed from injected seams so it is fully testable against a fake DB + fake
    embedder. In a real run the live dependency builder wires these to
    ``Source``/``reference`` creation and in-process ``embed_source_command`` inside an
    isolated Surreal namespace; B0C-B tests inject fakes (``NORMAL_DB_MUTATIONS = 0``).
    """

    def __init__(
        self,
        fx: FixturePN02,
        *,
        live_auth: object,
        source_creator: SourceCreator,
        reference_linker: ReferenceLinker,
        source_embedder: SourceEmbedder,
        notebook_record_ids: Mapping[str, str],
    ) -> None:
        require_live_provider_run_authorization(live_auth)
        self._fx = fx
        self._source_creator = source_creator
        self._reference_linker = reference_linker
        self._source_embedder = source_embedder
        self._notebook_record_ids = dict(notebook_record_ids)
        self._workload = derive_corpus_workload(fx)

    @property
    def workload(self) -> CorpusWorkload:
        return self._workload

    async def provision(self, *, run_id: str) -> ProvisionedCorpus:
        """Create sources + reference edges, then vectorize each Source ONCE (budgeted)."""
        budget = CorpusEmbeddingBudget(cap=self._workload.max_source_embedding_operations)

        # 1) create the 21 canonical Sources (deterministic order).
        record_id_by_key: Dict[str, str] = {}
        for src in sorted(self._fx.sources, key=lambda s: s.key):
            record_id = await self._source_creator(src.key, src.title, src.text)
            record_id_by_key[src.key] = record_id

        # 2) create the 24 reference membership edges.
        edges = 0
        for source_key, notebook_id in sorted(self._fx.memberships):
            nb_record = self._notebook_record_ids.get(notebook_id)
            if nb_record is None:
                raise CorpusProvisioningError(
                    f"no notebook record id for {notebook_id!r}"
                )
            await self._reference_linker(record_id_by_key[source_key], nb_record)
            edges += 1

        # 3) vectorize each canonical Source ONCE — corpus budget reserved BEFORE embed.
        ops = 0
        for src in sorted(self._fx.sources, key=lambda s: s.key):
            budget.reserve(1)  # BEFORE the embedding call (design §33)
            await self._source_embedder(record_id_by_key[src.key])
            ops += 1

        return ProvisionedCorpus(
            run_id=run_id,
            record_id_by_key=record_id_by_key,
            source_embedding_operations=ops,
            reference_edges=edges,
            workload=self._workload,
            budget_snapshot=budget.snapshot(),
        )


def build_in_isolation_corpus_seams(
    *,
    run_id: str,
) -> Dict[str, object]:  # pragma: no cover - live-only composition (not run in B0C-B)
    """Compose the REAL corpus seams over an ACTIVE isolated namespace (live-only).

    Returns callables that create ``Source`` records, ``reference`` edges, and populate
    ``source_embedding`` rows via in-process ``embed_source_command`` — all inside the
    already-open isolated namespace. It asserts active isolation first so it can never
    mutate the normal DB. NOT executed in B0C-B tests (the live driver assembles this
    only under a future authorized run); documented here so the real path is explicit.
    """
    from open_notebook.integrations.graphrag.eval.isolation08 import (
        require_active_isolation,
    )

    require_active_isolation()

    async def _create_source(key: str, title: str, text: str) -> str:
        from open_notebook.domain.notebook import Source

        source = Source(title=f"[{run_id}] {title}", full_text=text)
        await source.save()
        return str(source.id)

    async def _link_reference(source_record_id: str, notebook_record_id: str) -> None:
        from open_notebook.database.repository import ensure_record_id, repo_query

        await repo_query(
            "RELATE $src->reference->$nb",
            {
                "src": ensure_record_id(source_record_id),
                "nb": ensure_record_id(notebook_record_id),
            },
        )

    async def _embed_source(source_record_id: str) -> int:
        from commands.embedding_commands import (
            EmbedSourceInput,
            embed_source_command,
        )

        out = await embed_source_command(EmbedSourceInput(source_id=source_record_id))
        return int(getattr(out, "chunks_created", 0) or 0)

    return {
        "source_creator": _create_source,
        "reference_linker": _link_reference,
        "source_embedder": _embed_source,
    }


__all__ = [
    "SourceCreator",
    "ReferenceLinker",
    "SourceEmbedder",
    "CorpusBudgetExceeded",
    "CorpusProvisioningError",
    "CorpusWorkload",
    "derive_corpus_workload",
    "CorpusEmbeddingBudget",
    "ProvisionedCorpus",
    "RealPN02CorpusProvisioner",
    "build_in_isolation_corpus_seams",
]
