"""Load, validate, and freeze the GraphRAG-PN02 synthetic per-notebook fixture.

EVALUATION-ONLY. Pure I/O over JSON fixtures — no retriever, no DB, no network,
no provider. Nothing in production imports this module (the dependency direction
is eval -> production only; PN02A §12 ``PRODUCTION_IMPORTS_EVAL=NO``). It is a
NEW, separate loader from the frozen GraphRAG-08 ``dataset08.py`` (untouched).

The PN02 fixture ``graphrag_pn02_eval_v1`` (PN02A §3) is a per-notebook isolation
& QA benchmark: 3 notebooks, 21 canonical Sources (18 notebook-unique + 3 shared),
24 membership edges, 24 queries (8 classes × 3 notebooks). Its truth is authored
BEFORE any retriever and is content-safe (synthetic, deterministic, portable).

Integrity model (PN02A §16 — DIFFERENT from the 08 raw-byte hash): the freeze
hash is SHA-256 over a *canonical, sorted-key JSON serialization* of the logical
content {notebooks, source contents, membership matrix, query list + ground
truth}. Deterministic ordering, no timestamps, no derived routing/workspace ids
(those are computed elsewhere and are not fixture truth). ``compute_fixture_hash``
and ``verify_fixture_hash`` provide the freeze so a committed fixture cannot drift
once a provider-backed run has begun (PN02A §16/§63; task §13/§14/§63/§64).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple

FIXTURE_NAME = "graphrag_pn02_eval_v1"
NAMESPACE_TAG = "__graphrag_pn02_eval_v1__"

# Frozen shape (PN02A §3a / task §4). Any deviation is a design contradiction and
# must become graphrag_pn02_eval_v2 (task §64), never a silent edit.
FROZEN_NOTEBOOK_COUNT = 3
FROZEN_CANONICAL_SOURCE_COUNT = 21
FROZEN_UNIQUE_SOURCE_COUNT = 18
FROZEN_SHARED_SOURCE_COUNT = 3
FROZEN_WORKSPACE_MEMBERSHIP_COUNT = 24
FROZEN_SOURCES_PER_NOTEBOOK = 8
FROZEN_QUERY_COUNT = 24
FROZEN_QUERY_CLASS_COUNT = 8
FROZEN_QUERIES_PER_CLASS = 3  # one per notebook
FROZEN_NEGATIVE_COUNT = 3
FROZEN_MULTIHOP_COUNT = 3

NOTEBOOK_IDS: Tuple[str, ...] = ("NB_A", "NB_B", "NB_C")


class QueryClassPN02(str, Enum):
    DIRECT_LOCAL = "DIRECT_LOCAL"
    SEMANTIC_LOCAL = "SEMANTIC_LOCAL"
    MULTIHOP_LOCAL = "MULTIHOP_LOCAL"
    SHARED_SOURCE = "SHARED_SOURCE"
    CROSS_NOTEBOOK_COLLISION = "CROSS_NOTEBOOK_COLLISION"
    NEGATIVE_UNANSWERABLE = "NEGATIVE_UNANSWERABLE"
    RELATIONSHIP_CORROBORATION = "RELATIONSHIP_CORROBORATION"
    PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"


#: The one class whose queries are unanswerable (GT REQUIRED is the empty set).
NEGATIVE_CLASSES: FrozenSet[QueryClassPN02] = frozenset(
    {QueryClassPN02.NEGATIVE_UNANSWERABLE}
)


class FixturePN02Error(ValueError):
    """The PN02 fixture violates a structural or content invariant."""


@dataclass(frozen=True)
class NotebookPN02:
    notebook_id: str
    #: Opaque synthetic canonical record id. Workspace identity is derived from
    #: THIS (routing manifest, §40), never from the display theme (task §6).
    record_id: str
    theme: str


@dataclass(frozen=True)
class SourcePN02:
    key: str
    title: str
    text: str


@dataclass(frozen=True)
class QueryPN02:
    query_id: str
    notebook_id: str
    query_class: QueryClassPN02
    question: str
    answerable: bool
    required_source_ids: Tuple[str, ...]
    optional_support_source_ids: Tuple[str, ...]
    forbidden_source_ids: Tuple[str, ...]
    expected_answer_facts: Tuple[str, ...]
    forbidden_answer_facts: Tuple[str, ...]
    expected_abstention: bool
    required_citation_source_ids: Tuple[str, ...]
    multi_hop_required: bool
    #: Review-only justification. Never used by any retriever or metric.
    rationale: str

    @property
    def is_negative(self) -> bool:
        return not self.answerable


@dataclass(frozen=True)
class FixturePN02:
    fixture_version: str
    namespace_tag: str
    notebooks: Tuple[NotebookPN02, ...]
    sources: Tuple[SourcePN02, ...]
    #: (source_key, notebook_id) edges — the membership matrix.
    memberships: Tuple[Tuple[str, str], ...]
    queries: Tuple[QueryPN02, ...]

    @property
    def source_keys(self) -> FrozenSet[str]:
        return frozenset(s.key for s in self.sources)

    @property
    def notebook_ids(self) -> Tuple[str, ...]:
        return tuple(n.notebook_id for n in self.notebooks)

    def source_text(self, key: str) -> str:
        for s in self.sources:
            if s.key == key:
                return s.text
        raise FixturePN02Error(f"unknown source key {key!r}")

    def members_of(self, notebook_id: str) -> FrozenSet[str]:
        """Canonical current member Source ids of a notebook (M(N))."""
        return frozenset(
            src for (src, nb) in self.memberships if nb == notebook_id
        )

    def notebooks_of(self, source_key: str) -> FrozenSet[str]:
        """Notebooks that a Source legitimately belongs to."""
        return frozenset(
            nb for (src, nb) in self.memberships if src == source_key
        )

    def shared_source_keys(self) -> Tuple[str, ...]:
        return tuple(
            sorted(s.key for s in self.sources if len(self.notebooks_of(s.key)) >= 2)
        )

    def unique_source_keys(self) -> Tuple[str, ...]:
        return tuple(
            sorted(s.key for s in self.sources if len(self.notebooks_of(s.key)) == 1)
        )

    def queries_for_notebook(self, notebook_id: str) -> Tuple[QueryPN02, ...]:
        return tuple(q for q in self.queries if q.notebook_id == notebook_id)

    def query(self, query_id: str) -> QueryPN02:
        for q in self.queries:
            if q.query_id == query_id:
                return q
        raise FixturePN02Error(f"unknown query id {query_id!r}")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def default_fixture_dir() -> Path:
    """Repo-relative path to the committed PN02 fixture directory."""
    # .../eval/datasetpn02.py -> repo root is 5 parents up.
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "tests" / "fixtures" / FIXTURE_NAME


def _read_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise FixturePN02Error(f"fixture file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise FixturePN02Error(f"{path.name} must be a JSON object")
    return data


def _str_list(raw: object, ctx: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FixturePN02Error(f"{ctx} must be a list")
    out: List[str] = []
    for item in raw:
        s = str(item).strip()
        if not s:
            raise FixturePN02Error(f"{ctx} has an empty entry")
        if s in out:
            raise FixturePN02Error(f"{ctx} has duplicate entry {s!r}")
        out.append(s)
    return tuple(out)


def load_fixture(fixture_dir: Optional[Path] = None) -> FixturePN02:
    """Load and structurally validate the PN02 fixture.

    Raises ``FixturePN02Error`` on any structural violation. Does NOT enforce the
    frozen counts or the content invariants — call ``validate_fixture`` for the
    full gate (kept separate so unit tests can build tiny fixtures).
    """
    fixture_dir = fixture_dir or default_fixture_dir()
    corpus_raw = _read_json(fixture_dir / "corpus.json")
    queries_raw = _read_json(fixture_dir / "queries.json")

    if str(corpus_raw.get("fixture_version") or "") != FIXTURE_NAME:
        raise FixturePN02Error("corpus fixture_version mismatch")
    if str(queries_raw.get("fixture_version") or "") != FIXTURE_NAME:
        raise FixturePN02Error("queries fixture_version mismatch")
    if str(corpus_raw.get("namespace_tag") or "") != NAMESPACE_TAG:
        raise FixturePN02Error("corpus namespace_tag mismatch")
    if str(queries_raw.get("namespace_tag") or "") != NAMESPACE_TAG:
        raise FixturePN02Error("queries namespace_tag mismatch")

    notebooks = _parse_notebooks(corpus_raw)
    notebook_ids = {n.notebook_id for n in notebooks}
    sources = _parse_sources(corpus_raw)
    source_keys = {s.key for s in sources}
    memberships = _parse_memberships(corpus_raw, source_keys, notebook_ids)
    queries = _parse_queries(queries_raw, source_keys)

    return FixturePN02(
        fixture_version=FIXTURE_NAME,
        namespace_tag=NAMESPACE_TAG,
        notebooks=tuple(notebooks),
        sources=tuple(sources),
        memberships=tuple(memberships),
        queries=tuple(queries),
    )


def _parse_notebooks(corpus_raw: Mapping[str, object]) -> List[NotebookPN02]:
    raw = corpus_raw.get("notebooks")
    if not isinstance(raw, list) or not raw:
        raise FixturePN02Error("corpus.notebooks must be a non-empty list")
    out: List[NotebookPN02] = []
    seen: set[str] = set()
    seen_records: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixturePN02Error("each notebook must be an object")
        nb_id = str(entry.get("notebook_id") or "").strip()
        record_id = str(entry.get("record_id") or "").strip()
        theme = str(entry.get("theme") or "").strip()
        if not nb_id:
            raise FixturePN02Error("a notebook has an empty notebook_id")
        if nb_id in seen:
            raise FixturePN02Error(f"duplicate notebook_id {nb_id!r}")
        if not record_id:
            raise FixturePN02Error(f"notebook {nb_id} has an empty record_id")
        if record_id in seen_records:
            raise FixturePN02Error(f"duplicate notebook record_id {record_id!r}")
        seen.add(nb_id)
        seen_records.add(record_id)
        out.append(NotebookPN02(notebook_id=nb_id, record_id=record_id, theme=theme))
    return out


def _parse_sources(corpus_raw: Mapping[str, object]) -> List[SourcePN02]:
    raw = corpus_raw.get("sources")
    if not isinstance(raw, list) or not raw:
        raise FixturePN02Error("corpus.sources must be a non-empty list")
    out: List[SourcePN02] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixturePN02Error("each source must be an object")
        key = str(entry.get("key") or "").strip()
        title = str(entry.get("title") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not key:
            raise FixturePN02Error("a source has an empty key")
        if key in seen:
            raise FixturePN02Error(f"duplicate source key {key!r}")
        if not text:
            raise FixturePN02Error(f"source {key} has empty text")
        # A runtime SurrealDB RecordID must never be baked in as a canonical
        # Source key (task §6): logical keys only, no "source:" ids.
        if key.startswith("source:") or ":" in key:
            raise FixturePN02Error(
                f"source key {key!r} looks like a runtime record id; "
                "use a logical fixture key (e.g. A1, SH_AB)"
            )
        seen.add(key)
        out.append(SourcePN02(key=key, title=title, text=text))
    return out


def _parse_memberships(
    corpus_raw: Mapping[str, object],
    source_keys: set[str],
    notebook_ids: set[str],
) -> List[Tuple[str, str]]:
    raw = corpus_raw.get("memberships")
    if not isinstance(raw, list) or not raw:
        raise FixturePN02Error("corpus.memberships must be a non-empty list")
    out: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixturePN02Error("each membership must be an object")
        src = str(entry.get("source_key") or "").strip()
        nb = str(entry.get("notebook_id") or "").strip()
        if src not in source_keys:
            raise FixturePN02Error(f"membership references unknown source {src!r}")
        if nb not in notebook_ids:
            raise FixturePN02Error(f"membership references unknown notebook {nb!r}")
        edge = (src, nb)
        if edge in seen:
            raise FixturePN02Error(f"duplicate membership edge {edge!r}")
        seen.add(edge)
        out.append(edge)
    return out


def _parse_queries(
    queries_raw: Mapping[str, object], source_keys: set[str]
) -> List[QueryPN02]:
    raw = queries_raw.get("queries")
    if not isinstance(raw, list) or not raw:
        raise FixturePN02Error("queries.queries must be a non-empty list")
    out: List[QueryPN02] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise FixturePN02Error("each query must be an object")
        qid = str(entry.get("query_id") or "").strip()
        if not qid:
            raise FixturePN02Error("a query has an empty query_id")
        if qid in seen:
            raise FixturePN02Error(f"duplicate query_id {qid!r}")
        seen.add(qid)

        nb_id = str(entry.get("notebook_id") or "").strip()
        if not nb_id:
            raise FixturePN02Error(f"query {qid} has empty notebook_id")
        question = str(entry.get("question") or "").strip()
        if not question:
            raise FixturePN02Error(f"query {qid} has empty question")
        rationale = str(entry.get("rationale") or "").strip()
        if not rationale:
            raise FixturePN02Error(f"query {qid} has empty rationale")

        try:
            query_class = QueryClassPN02(str(entry.get("query_class")))
        except ValueError as exc:
            raise FixturePN02Error(
                f"query {qid} has invalid query_class {entry.get('query_class')!r}"
            ) from exc

        answerable = entry.get("answerable")
        if not isinstance(answerable, bool):
            raise FixturePN02Error(f"query {qid} answerable must be a bool")
        expected_abstention = entry.get("expected_abstention")
        if not isinstance(expected_abstention, bool):
            raise FixturePN02Error(f"query {qid} expected_abstention must be a bool")
        multi_hop = entry.get("multi_hop_required")
        if not isinstance(multi_hop, bool):
            raise FixturePN02Error(f"query {qid} multi_hop_required must be a bool")

        required = _str_list(entry.get("required_source_ids"), f"query {qid} required")
        optional = _str_list(
            entry.get("optional_support_source_ids"), f"query {qid} optional"
        )
        forbidden = _str_list(
            entry.get("forbidden_source_ids"), f"query {qid} forbidden"
        )
        citations = _str_list(
            entry.get("required_citation_source_ids"), f"query {qid} citations"
        )
        for group_name, group in (
            ("required", required),
            ("optional", optional),
            ("forbidden", forbidden),
            ("citation", citations),
        ):
            for sk in group:
                if sk not in source_keys:
                    raise FixturePN02Error(
                        f"query {qid} {group_name} references unknown source {sk!r}"
                    )

        expected_facts = _str_list(
            entry.get("expected_answer_facts"), f"query {qid} expected_facts"
        )
        forbidden_facts = _str_list(
            entry.get("forbidden_answer_facts"), f"query {qid} forbidden_facts"
        )

        out.append(
            QueryPN02(
                query_id=qid,
                notebook_id=nb_id,
                query_class=query_class,
                question=question,
                answerable=answerable,
                required_source_ids=required,
                optional_support_source_ids=optional,
                forbidden_source_ids=forbidden,
                expected_answer_facts=expected_facts,
                forbidden_answer_facts=forbidden_facts,
                expected_abstention=expected_abstention,
                required_citation_source_ids=citations,
                multi_hop_required=multi_hop,
                rationale=rationale,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Validation (task §12, §48)
# --------------------------------------------------------------------------- #

def validate_fixture(fx: FixturePN02) -> None:
    """Full frozen-shape + content-consistency gate (task §12/§48).

    Raises ``FixturePN02Error`` on the first inconsistency. Every check is
    computable from the fixture alone, before any retriever.
    """
    _validate_frozen_counts(fx)
    _validate_memberships(fx)
    _validate_query_shape(fx)
    _validate_ground_truth_membership(fx)
    _validate_answer_fact_grounding(fx)


def _validate_frozen_counts(fx: FixturePN02) -> None:
    if len(fx.notebooks) != FROZEN_NOTEBOOK_COUNT:
        raise FixturePN02Error(
            f"notebook_count {len(fx.notebooks)} != {FROZEN_NOTEBOOK_COUNT}"
        )
    if set(fx.notebook_ids) != set(NOTEBOOK_IDS):
        raise FixturePN02Error(
            f"notebook ids {sorted(fx.notebook_ids)} != {sorted(NOTEBOOK_IDS)}"
        )
    if len(fx.sources) != FROZEN_CANONICAL_SOURCE_COUNT:
        raise FixturePN02Error(
            f"canonical_source_count {len(fx.sources)} != {FROZEN_CANONICAL_SOURCE_COUNT}"
        )
    if len(fx.unique_source_keys()) != FROZEN_UNIQUE_SOURCE_COUNT:
        raise FixturePN02Error(
            f"unique_source_count {len(fx.unique_source_keys())} != {FROZEN_UNIQUE_SOURCE_COUNT}"
        )
    if len(fx.shared_source_keys()) != FROZEN_SHARED_SOURCE_COUNT:
        raise FixturePN02Error(
            f"shared_source_count {len(fx.shared_source_keys())} != {FROZEN_SHARED_SOURCE_COUNT}"
        )
    if len(fx.memberships) != FROZEN_WORKSPACE_MEMBERSHIP_COUNT:
        raise FixturePN02Error(
            f"membership_count {len(fx.memberships)} != {FROZEN_WORKSPACE_MEMBERSHIP_COUNT}"
        )
    for nb in fx.notebook_ids:
        n = len(fx.members_of(nb))
        if n != FROZEN_SOURCES_PER_NOTEBOOK:
            raise FixturePN02Error(
                f"notebook {nb} has {n} members != {FROZEN_SOURCES_PER_NOTEBOOK}"
            )
    if len(fx.queries) != FROZEN_QUERY_COUNT:
        raise FixturePN02Error(
            f"query_count {len(fx.queries)} != {FROZEN_QUERY_COUNT}"
        )
    negatives = sum(1 for q in fx.queries if q.is_negative)
    if negatives != FROZEN_NEGATIVE_COUNT:
        raise FixturePN02Error(f"negative_count {negatives} != {FROZEN_NEGATIVE_COUNT}")
    multihop = sum(1 for q in fx.queries if q.multi_hop_required)
    if multihop != FROZEN_MULTIHOP_COUNT:
        raise FixturePN02Error(f"multihop_count {multihop} != {FROZEN_MULTIHOP_COUNT}")
    # 8 classes × exactly one per notebook.
    classes = {q.query_class for q in fx.queries}
    if len(classes) != FROZEN_QUERY_CLASS_COUNT:
        raise FixturePN02Error(
            f"query_class_count {len(classes)} != {FROZEN_QUERY_CLASS_COUNT}"
        )
    for nb in fx.notebook_ids:
        nb_queries = fx.queries_for_notebook(nb)
        if len(nb_queries) != FROZEN_QUERY_CLASS_COUNT:
            raise FixturePN02Error(
                f"notebook {nb} has {len(nb_queries)} queries != {FROZEN_QUERY_CLASS_COUNT}"
            )
        nb_classes = [q.query_class for q in nb_queries]
        if len(set(nb_classes)) != FROZEN_QUERY_CLASS_COUNT:
            raise FixturePN02Error(
                f"notebook {nb} does not have exactly one query per class"
            )
    for qc in QueryClassPN02:
        n = sum(1 for q in fx.queries if q.query_class is qc)
        if n != FROZEN_QUERIES_PER_CLASS:
            raise FixturePN02Error(
                f"class {qc.value} has {n} queries != {FROZEN_QUERIES_PER_CLASS}"
            )


def _validate_memberships(fx: FixturePN02) -> None:
    for s in fx.sources:
        owners = fx.notebooks_of(s.key)
        if not owners:
            raise FixturePN02Error(f"source {s.key} belongs to no notebook")
        if len(owners) > 2:
            raise FixturePN02Error(
                f"source {s.key} belongs to {len(owners)} notebooks (>2 unsupported)"
            )
    # Shared sources must be exactly the 3 pairwise SH_* sources.
    shared = set(fx.shared_source_keys())
    for sk in shared:
        if len(fx.notebooks_of(sk)) != 2:
            raise FixturePN02Error(f"shared source {sk} must belong to exactly 2 notebooks")


def _validate_query_shape(fx: FixturePN02) -> None:
    for q in fx.queries:
        if q.notebook_id not in set(fx.notebook_ids):
            raise FixturePN02Error(
                f"query {q.query_id} references unknown notebook {q.notebook_id!r}"
            )
        is_negative_class = q.query_class in NEGATIVE_CLASSES
        if q.answerable == is_negative_class:
            raise FixturePN02Error(
                f"query {q.query_id}: answerable={q.answerable} disagrees with "
                f"negative class membership ({q.query_class.value})"
            )
        if q.is_negative:
            if q.required_source_ids:
                raise FixturePN02Error(
                    f"negative query {q.query_id} must have empty REQUIRED"
                )
            if not q.expected_abstention:
                raise FixturePN02Error(
                    f"negative query {q.query_id} must set expected_abstention=true"
                )
            if q.expected_answer_facts:
                raise FixturePN02Error(
                    f"negative query {q.query_id} must have no expected_answer_facts"
                )
            if q.required_citation_source_ids:
                raise FixturePN02Error(
                    f"negative query {q.query_id} must have no required citations"
                )
        else:
            if not q.required_source_ids:
                raise FixturePN02Error(
                    f"answerable query {q.query_id} must have >=1 required source"
                )
            if q.expected_abstention:
                raise FixturePN02Error(
                    f"answerable query {q.query_id} must not expect abstention"
                )
            if not q.expected_answer_facts:
                raise FixturePN02Error(
                    f"answerable query {q.query_id} must have >=1 expected_answer_fact"
                )
        # Multi-hop invariant R1 (PN02A §7): |REQUIRED| >= 2.
        if q.multi_hop_required and len(q.required_source_ids) < 2:
            raise FixturePN02Error(
                f"multi-hop query {q.query_id} must have >=2 required sources"
            )
        if (
            q.query_class is QueryClassPN02.MULTIHOP_LOCAL
            and not q.multi_hop_required
        ):
            raise FixturePN02Error(
                f"MULTIHOP_LOCAL query {q.query_id} must set multi_hop_required=true"
            )
        # No source both REQUIRED and OPTIONAL / both REQUIRED and FORBIDDEN /
        # both OPTIONAL and FORBIDDEN (task §12).
        req = set(q.required_source_ids)
        opt = set(q.optional_support_source_ids)
        forb = set(q.forbidden_source_ids)
        if req & opt:
            raise FixturePN02Error(
                f"query {q.query_id}: {sorted(req & opt)} are BOTH required and optional"
            )
        if req & forb:
            raise FixturePN02Error(
                f"query {q.query_id}: {sorted(req & forb)} are BOTH required and forbidden"
            )
        if opt & forb:
            raise FixturePN02Error(
                f"query {q.query_id}: {sorted(opt & forb)} are BOTH optional and forbidden"
            )


def _validate_ground_truth_membership(fx: FixturePN02) -> None:
    """REQUIRED/OPTIONAL/citations are members; FORBIDDEN are non-members (§12)."""
    for q in fx.queries:
        members = fx.members_of(q.notebook_id)
        for sk in q.required_source_ids:
            if sk not in members:
                raise FixturePN02Error(
                    f"query {q.query_id} required source {sk} is not a member of {q.notebook_id}"
                )
        for sk in q.optional_support_source_ids:
            if sk not in members:
                raise FixturePN02Error(
                    f"query {q.query_id} optional source {sk} is not a member of {q.notebook_id}"
                )
        for sk in q.required_citation_source_ids:
            if sk not in members:
                raise FixturePN02Error(
                    f"query {q.query_id} citation source {sk} is not a member of {q.notebook_id}"
                )
        # Citations must be a subset of required ∪ optional (you may only require a
        # citation to evidence you also authored as relevant).
        allowed_cite = set(q.required_source_ids) | set(q.optional_support_source_ids)
        stray = set(q.required_citation_source_ids) - allowed_cite
        if stray:
            raise FixturePN02Error(
                f"query {q.query_id} citations {sorted(stray)} are neither required nor optional"
            )
        for sk in q.forbidden_source_ids:
            if sk in members:
                raise FixturePN02Error(
                    f"query {q.query_id} forbidden source {sk} IS a member of {q.notebook_id} "
                    "(a member Source can never be forbidden — that would be legitimate evidence)"
                )
        # Collision queries must forbid the specific collision counterpart(s) (R3).
        if q.query_class is QueryClassPN02.CROSS_NOTEBOOK_COLLISION and not forbidden_nonempty(q):
            raise FixturePN02Error(
                f"collision query {q.query_id} must list >=1 forbidden collision source"
            )


def forbidden_nonempty(q: QueryPN02) -> bool:
    return len(q.forbidden_source_ids) > 0


def _validate_answer_fact_grounding(fx: FixturePN02) -> None:
    """Expected tokens live in member sources; forbidden tokens live ONLY in
    non-member sources (task §7/§8/§30). Makes answer-leakage detection sound.
    """
    for q in fx.queries:
        members = fx.members_of(q.notebook_id)
        member_text = " \n ".join(fx.source_text(k) for k in sorted(members))
        # Expected facts must appear in the union of the query's REQUIRED members
        # (the minimal answer evidence), so a correct answer is grounded.
        required_text = " \n ".join(
            fx.source_text(k) for k in sorted(q.required_source_ids)
        )
        for token in q.expected_answer_facts:
            if token not in required_text:
                raise FixturePN02Error(
                    f"query {q.query_id} expected fact {token!r} not present in its "
                    "required member Sources"
                )
        # Forbidden facts must NOT be derivable from any member Source (else the
        # 'answer leaked from a non-member' semantics is unsound) and MUST be
        # present in at least one forbidden non-member Source.
        for token in q.forbidden_answer_facts:
            if token in member_text:
                raise FixturePN02Error(
                    f"query {q.query_id} forbidden fact {token!r} IS present in a "
                    f"member Source of {q.notebook_id} — it cannot mark a leak"
                )
            forbidden_text = " \n ".join(
                fx.source_text(k) for k in sorted(q.forbidden_source_ids)
            )
            if token not in forbidden_text:
                raise FixturePN02Error(
                    f"query {q.query_id} forbidden fact {token!r} not present in any "
                    "listed forbidden Source"
                )


# --------------------------------------------------------------------------- #
# Membership-removal scenario (PN02A §11a / task §32)
# --------------------------------------------------------------------------- #

REMOVAL_SHARED_SOURCE = "SH_AB"
REMOVAL_FROM_NOTEBOOK = "NB_A"
REMOVAL_RETAINED_NOTEBOOK = "NB_B"


@dataclass(frozen=True)
class MembershipRemovalScenario:
    """Frozen offline lifecycle transition: remove SH_AB from NB_A, keep in NB_B.

    Represents canonical membership before/after and the expected graph-delete
    target — deterministically derived from the fixture, never a separate truth
    file (task §32). The removal is edge-only in ON plus a per-workspace derived
    graph delete from ``expected_graph_delete_workspace`` (PN02A §11a).
    """

    shared_source: str
    removed_from_notebook: str
    retained_notebook: str
    members_before_removed_nb: FrozenSet[str]
    members_after_removed_nb: FrozenSet[str]
    members_before_retained_nb: FrozenSet[str]
    members_after_retained_nb: FrozenSet[str]
    expected_graph_delete_workspace_notebook: str
    #: The two SHARED_SOURCE re-probe queries (one in the removed nb, one in the
    #: retained nb) — PN02A §13 removal re-probe pair.
    reprobe_query_ids: Tuple[str, ...]


def membership_removal_scenario(fx: FixturePN02) -> MembershipRemovalScenario:
    shared = REMOVAL_SHARED_SOURCE
    if shared not in fx.source_keys:
        raise FixturePN02Error(f"removal source {shared} absent from fixture")
    owners = fx.notebooks_of(shared)
    if {REMOVAL_FROM_NOTEBOOK, REMOVAL_RETAINED_NOTEBOOK} - owners:
        raise FixturePN02Error(
            f"removal precondition violated: {shared} must belong to both "
            f"{REMOVAL_FROM_NOTEBOOK} and {REMOVAL_RETAINED_NOTEBOOK}"
        )
    before_removed = fx.members_of(REMOVAL_FROM_NOTEBOOK)
    before_retained = fx.members_of(REMOVAL_RETAINED_NOTEBOOK)
    after_removed = frozenset(before_removed - {shared})
    after_retained = frozenset(before_retained)  # unchanged

    # The two SHARED_SOURCE queries that reference the removed shared source, one
    # per involved notebook.
    reprobe = tuple(
        q.query_id
        for q in fx.queries
        if q.query_class is QueryClassPN02.SHARED_SOURCE
        and q.notebook_id in (REMOVAL_FROM_NOTEBOOK, REMOVAL_RETAINED_NOTEBOOK)
        and shared in q.required_source_ids
    )
    if len(reprobe) != 2:
        raise FixturePN02Error(
            f"removal scenario expects exactly 2 SHARED_SOURCE re-probe queries "
            f"referencing {shared}, found {len(reprobe)}"
        )
    return MembershipRemovalScenario(
        shared_source=shared,
        removed_from_notebook=REMOVAL_FROM_NOTEBOOK,
        retained_notebook=REMOVAL_RETAINED_NOTEBOOK,
        members_before_removed_nb=before_removed,
        members_after_removed_nb=after_removed,
        members_before_retained_nb=before_retained,
        members_after_retained_nb=after_retained,
        expected_graph_delete_workspace_notebook=REMOVAL_FROM_NOTEBOOK,
        reprobe_query_ids=reprobe,
    )


# --------------------------------------------------------------------------- #
# Canonical serialization + hash (PN02A §16 / task §13/§14/§63)
# --------------------------------------------------------------------------- #

def canonical_payload(fx: FixturePN02) -> Dict[str, object]:
    """Deterministic logical view hashed for the freeze marker.

    Contains {fixture_version, notebooks, source contents, membership matrix,
    query list + ground truth} — the fixture truth. NO timestamps, NO derived
    routing/workspace ids, NO rationale-free content omitted. Sorted throughout so
    serialization is stable across machines and Python runs.
    """
    return {
        "fixture_version": fx.fixture_version,
        "namespace_tag": fx.namespace_tag,
        "notebooks": [
            {"notebook_id": n.notebook_id, "record_id": n.record_id, "theme": n.theme}
            for n in sorted(fx.notebooks, key=lambda n: n.notebook_id)
        ],
        "sources": [
            {"key": s.key, "title": s.title, "text": s.text}
            for s in sorted(fx.sources, key=lambda s: s.key)
        ],
        "memberships": [
            [src, nb] for (src, nb) in sorted(fx.memberships)
        ],
        "queries": [
            {
                "query_id": q.query_id,
                "notebook_id": q.notebook_id,
                "query_class": q.query_class.value,
                "question": q.question,
                "answerable": q.answerable,
                "required_source_ids": list(q.required_source_ids),
                "optional_support_source_ids": list(q.optional_support_source_ids),
                "forbidden_source_ids": list(q.forbidden_source_ids),
                "expected_answer_facts": list(q.expected_answer_facts),
                "forbidden_answer_facts": list(q.forbidden_answer_facts),
                "expected_abstention": q.expected_abstention,
                "required_citation_source_ids": list(q.required_citation_source_ids),
                "multi_hop_required": q.multi_hop_required,
                "rationale": q.rationale,
            }
            for q in sorted(fx.queries, key=lambda q: q.query_id)
        ],
    }


def canonical_serialization(fx: FixturePN02) -> str:
    """Canonical, sorted-key JSON string (PN02A §16). Reproducible."""
    return json.dumps(
        canonical_payload(fx),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def compute_fixture_hash(fx: FixturePN02) -> str:
    """SHA-256 of the canonical serialization (PN02A §16)."""
    return hashlib.sha256(canonical_serialization(fx).encode("utf-8")).hexdigest()


def compute_integrity(fixture_dir: Optional[Path] = None) -> Dict[str, object]:
    """Content-free freeze marker payload (hash + counts, never source text)."""
    fx = load_fixture(fixture_dir)
    return {
        "fixture_name": FIXTURE_NAME,
        "namespace_tag": NAMESPACE_TAG,
        "fixture_sha256": compute_fixture_hash(fx),
        "notebook_count": len(fx.notebooks),
        "canonical_source_count": len(fx.sources),
        "unique_source_count": len(fx.unique_source_keys()),
        "shared_source_count": len(fx.shared_source_keys()),
        "workspace_membership_count": len(fx.memberships),
        "query_count": len(fx.queries),
        "negative_count": sum(1 for q in fx.queries if q.is_negative),
        "multihop_count": sum(1 for q in fx.queries if q.multi_hop_required),
    }


def load_freeze(fixture_dir: Optional[Path] = None) -> Dict[str, object]:
    fixture_dir = fixture_dir or default_fixture_dir()
    return _read_json(fixture_dir / "freeze.json")


def verify_fixture_hash(fixture_dir: Optional[Path] = None) -> Tuple[bool, str]:
    """Compare the live fixture hash against the committed freeze marker.

    Returns (ok, detail). ok=False means the fixture has diverged from its frozen
    hash — a hard stop for any provider-backed run (task §14/§64).
    """
    fixture_dir = fixture_dir or default_fixture_dir()
    freeze = load_freeze(fixture_dir)
    live = compute_integrity(fixture_dir)
    frozen_hash = freeze.get("fixture_sha256")
    live_hash = live["fixture_sha256"]
    if frozen_hash != live_hash:
        return False, (
            f"fixture_sha256 mismatch: frozen={frozen_hash!r} live={live_hash!r}"
        )
    return True, str(live_hash)


__all__ = [
    "FIXTURE_NAME",
    "NAMESPACE_TAG",
    "FROZEN_NOTEBOOK_COUNT",
    "FROZEN_CANONICAL_SOURCE_COUNT",
    "FROZEN_UNIQUE_SOURCE_COUNT",
    "FROZEN_SHARED_SOURCE_COUNT",
    "FROZEN_WORKSPACE_MEMBERSHIP_COUNT",
    "FROZEN_SOURCES_PER_NOTEBOOK",
    "FROZEN_QUERY_COUNT",
    "FROZEN_QUERY_CLASS_COUNT",
    "FROZEN_NEGATIVE_COUNT",
    "FROZEN_MULTIHOP_COUNT",
    "NOTEBOOK_IDS",
    "QueryClassPN02",
    "NEGATIVE_CLASSES",
    "NotebookPN02",
    "SourcePN02",
    "QueryPN02",
    "FixturePN02",
    "FixturePN02Error",
    "load_fixture",
    "validate_fixture",
    "default_fixture_dir",
    "MembershipRemovalScenario",
    "membership_removal_scenario",
    "REMOVAL_SHARED_SOURCE",
    "REMOVAL_FROM_NOTEBOOK",
    "REMOVAL_RETAINED_NOTEBOOK",
    "canonical_payload",
    "canonical_serialization",
    "compute_fixture_hash",
    "compute_integrity",
    "load_freeze",
    "verify_fixture_hash",
]
