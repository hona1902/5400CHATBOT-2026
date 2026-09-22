"""PN02D-B3P generation-evidence-materialization treatment — provider-free tests.

Proves the ONE isolated causal variable (final-answer evidence materialization):
CONTROL stays Source-ids-only (byte-identical to B3M); TREATMENT adds bounded source
CONTENT for exactly the same selected, member-filtered, ordered evidence ids. Covers the
materializer fail-closed/boundary/truncation/cap semantics, the runtime resolver mapping +
notebook boundary (mocked, no provider), causal-freeze of selected ids across arms, and
observer/logging content-safety. ZERO provider traffic — the completion transport and the
runtime source-text fetcher are injected fakes/mocks.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from open_notebook.integrations.graphrag.eval.datasetpn02 import load_fixture
from open_notebook.integrations.graphrag.eval.evidence_materialization_pn02d import (
    MAX_CHARS_PER_SOURCE,
    MAX_EVIDENCE_ITEMS_PER_ARM,
    EvidenceItem,
    EvidenceMaterializationError,
    EvidenceMaterializer,
    build_runtime_evidence_content_resolver,
    build_runtime_evidence_materializer,
)
from open_notebook.integrations.graphrag.eval.normalizepn02 import (
    normalize_graph,
    normalize_vector,
)
from open_notebook.integrations.graphrag.eval.qastagepn02db2 import (
    ARM_ORDER,
    B2QAStage,
    bind_treatment_materializer,
)
from open_notebook.integrations.graphrag.eval.real_final_answer_seam_pn02d import (
    RealFinalAnswerSeam,
    build_final_answer_prompt,
    build_final_answer_prompt_with_content,
)
from open_notebook.integrations.graphrag.eval.realseamsb2pn02d import build_b2_qa_stage
from open_notebook.integrations.graphrag.eval.schemaspn02 import (
    GDEvidenceResult,
    VectorEvidenceResult,
)

_CANNED_COMPLETION = "ANSWER\nCITATIONS: A1\nABSTAIN: NO"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_fixture_resolver(fx):
    """A TEST-ONLY fixture resolver (uses fx.source_text). Isolated from runtime."""

    async def _resolve(notebook_id, source_id):
        if source_id in fx.source_keys:
            return fx.source_text(source_id)
        return None

    return _resolve


class _RecordingCompletion:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return _CANNED_COMPLETION


class _RecordingMaterializer:
    """Wraps a real EvidenceMaterializer, recording (notebook_id, source_ids) per call."""

    def __init__(self, inner: EvidenceMaterializer) -> None:
        self._inner = inner
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def materialize(self, notebook_id, source_ids, *, allowed_source_ids):
        self.calls.append((notebook_id, tuple(source_ids)))
        return await self._inner.materialize(
            notebook_id, source_ids, allowed_source_ids=allowed_source_ids
        )


def _all_query_results(fx):
    ordered = sorted(fx.queries, key=lambda q: q.query_id)
    vres = {
        q.query_id: VectorEvidenceResult(
            query_id=q.query_id,
            notebook_id=q.notebook_id,
            evidence=normalize_vector(
                sorted(fx.members_of(q.notebook_id)), allowlist=fx.source_keys
            ),
        )
        for q in ordered
    }
    gres = {
        q.query_id: GDEvidenceResult(
            query_id=q.query_id,
            notebook_id=q.notebook_id,
            evidence=normalize_graph(
                sorted(fx.members_of(q.notebook_id)), allowlist=fx.source_keys
            ),
        )
        for q in ordered
    }
    return ordered, vres, gres


# --------------------------------------------------------------------------- #
# Prompt-level: control byte-identical / treatment content (§20-§23, §39-§41, §48)
# --------------------------------------------------------------------------- #

def test_control_prompt_is_source_ids_only():
    p = build_final_answer_prompt(question="Q?", evidence_source_ids=["A1", "A2"])
    assert "Evidence Source ids: A1, A2" in p
    assert "[Source:" not in p
    # locked byte-identity to the pre-B3P prompt shape
    assert p == (
        "You are the Open Notebook answerer. Answer the question using ONLY the "
        "evidence Sources listed below, and cite ONLY those Source ids. If the "
        "evidence does not answer the question, abstain.\n\n"
        "Question: Q?\n"
        "Evidence Source ids: A1, A2\n\n"
        "Respond with the answer text, then two final lines exactly:\n"
        "CITATIONS: <comma-separated Source ids you used, or empty>\n"
        "ABSTAIN: <YES or NO>\n"
    )


def test_treatment_prompt_materializes_content_in_order():
    items = [EvidenceItem("A1", "text-A"), EvidenceItem("A2", "text-B")]
    t = build_final_answer_prompt_with_content(question="Q?", evidence_items=items)
    assert "[Source: A1] text-A" in t and "[Source: A2] text-B" in t
    # order preserved (A1 block before A2 block)
    assert t.index("[Source: A1]") < t.index("[Source: A2]")
    # citation ids remain visible
    assert "A1" in t and "A2" in t


def test_instruction_text_identical_control_vs_treatment():
    p = build_final_answer_prompt(question="Q?", evidence_source_ids=["A1"])
    t = build_final_answer_prompt_with_content(
        question="Q?", evidence_items=[EvidenceItem("A1", "x")]
    )
    # header (before "Question:") and footer (the CITATIONS/ABSTAIN contract) identical
    assert p.split("Question:")[0] == t.split("Question:")[0]
    assert p[p.index("Respond with"):] == t[t.index("Respond with"):]


# --------------------------------------------------------------------------- #
# Materializer semantics (§42-§47, §56)
# --------------------------------------------------------------------------- #

def test_materialize_preserves_order_and_only_selected():
    async def resolver(nb, sid):
        return {"A": "txtA", "B": "txtB", "C": "txtC"}.get(sid)

    m = EvidenceMaterializer(resolver=resolver)
    items = asyncio.run(
        m.materialize("NB", ["C", "A"], allowed_source_ids=frozenset({"A", "B", "C"}))
    )
    assert [it.source_id for it in items] == ["C", "A"]
    assert [it.content for it in items] == ["txtC", "txtA"]
    # unselected "B" never appears
    assert "B" not in {it.source_id for it in items}


def test_non_member_source_fails_closed():
    async def resolver(nb, sid):
        return "txt"

    m = EvidenceMaterializer(resolver=resolver)
    with pytest.raises(EvidenceMaterializationError):
        asyncio.run(m.materialize("NB", ["A", "X"], allowed_source_ids=frozenset({"A"})))


def test_missing_source_fails_closed():
    async def resolver(nb, sid):
        return None  # source cannot be resolved

    m = EvidenceMaterializer(resolver=resolver)
    with pytest.raises(EvidenceMaterializationError):
        asyncio.run(m.materialize("NB", ["A"], allowed_source_ids=frozenset({"A"})))


def test_empty_content_fails_closed():
    async def resolver(nb, sid):
        return ""

    m = EvidenceMaterializer(resolver=resolver)
    with pytest.raises(EvidenceMaterializationError):
        asyncio.run(m.materialize("NB", ["A"], allowed_source_ids=frozenset({"A"})))


def test_head_preserving_drop_tail_truncation_retains_id():
    # Head-preserving (drop-tail) truncation: keep the FIRST N chars (content[:N]),
    # drop the tail, retain the source id. Unambiguous per PN02D-B3P-R1 M2.
    head = "H" * 5
    tail = "T" * 45
    content = head + tail

    async def resolver(nb, sid):
        return content

    m = EvidenceMaterializer(resolver=resolver, max_chars_per_source=5)
    items = asyncio.run(m.materialize("NB", ["A"], allowed_source_ids=frozenset({"A"})))
    assert items[0].source_id == "A"           # source id retained
    assert items[0].content == head            # first N kept
    assert items[0].content == content[:5]     # == content[:N]
    assert tail not in items[0].content        # tail dropped
    assert len(items[0].content) == 5


def test_item_cap_fails_closed():
    async def resolver(nb, sid):
        return "t"

    ids = [f"S{i}" for i in range(MAX_EVIDENCE_ITEMS_PER_ARM + 1)]
    m = EvidenceMaterializer(resolver=resolver)
    with pytest.raises(EvidenceMaterializationError):
        asyncio.run(m.materialize("NB", ids, allowed_source_ids=frozenset(ids)))


def test_no_dedup_preserves_given_sequence():
    async def resolver(nb, sid):
        return "t"

    m = EvidenceMaterializer(resolver=resolver)
    items = asyncio.run(m.materialize("NB", ["A", "A"], allowed_source_ids=frozenset({"A"})))
    assert [it.source_id for it in items] == ["A", "A"]  # 1:1 map, no dedup introduced


# --------------------------------------------------------------------------- #
# Runtime resolver (§11, §55, §56) — mocked, no provider/network
# --------------------------------------------------------------------------- #

def test_runtime_resolver_maps_key_to_full_text():
    async def fetcher(record_id):
        return {"source:x": "full-A1"}.get(record_id)

    resolve = build_runtime_evidence_content_resolver({"A1": "source:x"}, fetcher)

    async def _resolve_key(key: str):
        return await resolve("NB_A", key)

    assert asyncio.run(_resolve_key("A1")) == "full-A1"
    # a key with no provisioned record -> None (fail-closed upstream)
    assert asyncio.run(_resolve_key("ZZ")) is None


def test_runtime_materializer_end_to_end_with_mock_fetcher():
    async def fetcher(record_id):
        return {"source:x": "full-A1"}.get(record_id)

    m = build_runtime_evidence_materializer({"A1": "source:x"}, source_text_fetcher=fetcher)
    items = asyncio.run(m.materialize("NB_A", ["A1"], allowed_source_ids=frozenset({"A1"})))
    assert items == (EvidenceItem("A1", "full-A1"),)


def test_runtime_resolver_notebook_boundary_never_fetches_non_member():
    fetched: list[str] = []

    async def fetcher(record_id):
        fetched.append(record_id)
        return "content"

    # A1 (member) and X1 (foreign) both have provisioned records, but X1 is NOT allowed.
    m = build_runtime_evidence_materializer(
        {"A1": "source:a", "X1": "source:x"}, source_text_fetcher=fetcher
    )
    with pytest.raises(EvidenceMaterializationError):
        asyncio.run(
            m.materialize("NB_A", ["A1", "X1"], allowed_source_ids=frozenset({"A1"}))
        )
    # boundary check happens BEFORE any fetch of the foreign record
    assert "source:x" not in fetched


# --------------------------------------------------------------------------- #
# Stage-level: control vs treatment over the frozen fixture (§22, §49-§53)
# --------------------------------------------------------------------------- #

def test_stage_control_uses_source_ids_only_prompt():
    fx = load_fixture()
    rec = _RecordingCompletion()
    stage = B2QAStage(answer_seam=RealFinalAnswerSeam(completion_fn=rec))
    ordered, vres, gres = _all_query_results(fx)
    asyncio.run(stage(fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres))
    assert len(rec.prompts) == 72
    assert all("Evidence Source ids:" in p for p in rec.prompts)
    assert all("[Source:" not in p for p in rec.prompts)


def test_stage_treatment_materializes_content_prompt():
    fx = load_fixture()
    rec = _RecordingCompletion()
    stage = B2QAStage(
        answer_seam=RealFinalAnswerSeam(completion_fn=rec),
        evidence_materializer=EvidenceMaterializer(resolver=_make_fixture_resolver(fx)),
    )
    ordered, vres, gres = _all_query_results(fx)
    asyncio.run(stage(fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres))
    assert len(rec.prompts) == 72
    # every answerable arm prompt carries materialized content blocks
    with_content = [p for p in rec.prompts if "[Source:" in p]
    assert len(with_content) == 72
    # a known member's content appears verbatim in at least one treatment prompt
    a1_text = fx.source_text("A1")
    assert any(a1_text in p for p in rec.prompts)


def test_control_and_treatment_select_identical_ids_per_arm():
    fx = load_fixture()
    ordered, vres, gres = _all_query_results(fx)
    # The plan is deterministic and materializer-independent → the selected ids.
    plan = B2QAStage(answer_seam=RealFinalAnswerSeam(completion_fn=_RecordingCompletion())).plan(
        fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres
    )
    expected = [(it.query_id, it.arm, it.evidence_source_ids) for it in plan]

    # TREATMENT run: a recording materializer captures exactly what it is asked to resolve.
    recmat = _RecordingMaterializer(EvidenceMaterializer(resolver=_make_fixture_resolver(fx)))
    stage = B2QAStage(
        answer_seam=RealFinalAnswerSeam(completion_fn=_RecordingCompletion()),
        evidence_materializer=recmat,  # type: ignore[arg-type]
    )
    asyncio.run(stage(fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres))
    materialized_seq = [ids for (_nb, ids) in recmat.calls]
    assert materialized_seq == [ids for (_q, _a, ids) in expected]
    # per-arm causal freeze: materialized ids ⊆ notebook members for every call
    for (nb, ids), (_q, _a, sel) in zip(recmat.calls, expected):
        assert ids == sel
        assert set(ids) <= set(fx.members_of(nb))


def test_treatment_never_materializes_non_member():
    fx = load_fixture()
    nb = sorted({q.notebook_id for q in fx.queries})[0]
    non_member = next(s for s in fx.source_keys if s not in fx.members_of(nb))
    q = fx.queries_for_notebook(nb)[0]
    members = sorted(fx.members_of(nb))
    vres = {
        q.query_id: VectorEvidenceResult(
            query_id=q.query_id,
            notebook_id=nb,
            evidence=normalize_vector(
                [members[0], non_member], allowlist=fx.source_keys
            ),
        )
    }
    gres = {
        q.query_id: GDEvidenceResult(
            query_id=q.query_id,
            notebook_id=nb,
            evidence=normalize_graph([members[1], non_member], allowlist=fx.source_keys),
        )
    }
    rec = _RecordingCompletion()
    stage = B2QAStage(
        answer_seam=RealFinalAnswerSeam(completion_fn=rec),
        evidence_materializer=EvidenceMaterializer(resolver=_make_fixture_resolver(fx)),
    )
    asyncio.run(stage(fx=fx, ordered_queries=[q], vector_results=vres, gd_results=gres))
    # non-member text must never appear in any materialized prompt
    assert all(fx.source_text(non_member) not in p for p in rec.prompts)


def test_observer_record_has_no_raw_source_content():
    fx = load_fixture()
    records: list = []
    stage = B2QAStage(
        answer_seam=RealFinalAnswerSeam(completion_fn=_RecordingCompletion()),
        evidence_materializer=EvidenceMaterializer(resolver=_make_fixture_resolver(fx)),
        execution_observer=records.append,
    )
    ordered, vres, gres = _all_query_results(fx)
    asyncio.run(stage(fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres))
    assert len(records) == 72
    for r in records:
        # the record carries Source IDS (not content) and no EvidenceItem/content field
        assert not hasattr(r, "evidence_items")
        assert not hasattr(r, "content")
        for sid in r.evidence_source_ids:
            assert sid in fx.source_keys  # ids, never raw text
            # the record must not smuggle the source's raw text anywhere obvious
            assert fx.source_text(sid) != sid


def test_materialization_does_not_log_source_content(caplog):
    async def resolver(nb, sid):
        return "SUPER_SECRET_SOURCE_TEXT"

    m = EvidenceMaterializer(resolver=resolver)
    with caplog.at_level("DEBUG"):
        asyncio.run(m.materialize("NB", ["A"], allowed_source_ids=frozenset({"A"})))
    assert "SUPER_SECRET_SOURCE_TEXT" not in caplog.text


def test_bounds_constants_frozen():
    assert MAX_CHARS_PER_SOURCE == 2000
    assert MAX_EVIDENCE_ITEMS_PER_ARM == 8
    assert len(ARM_ORDER) == 3


# --------------------------------------------------------------------------- #
# PN02D-B3P-R1 M1: governed treatment live-wiring (post-provision, no code edit)
# --------------------------------------------------------------------------- #

def _fake_record_id_by_key(fx):
    return {k: f"source:{k}" for k in fx.source_keys}


def _fake_fetcher_over_fixture(fx):
    table = {f"source:{k}": fx.source_text(k) for k in fx.source_keys}

    async def _fetch(record_id):
        return table.get(record_id)

    return _fetch


def test_governed_treatment_wiring_binds_after_provision_and_materializes():
    # Exercises the EXACT post-provision path the live driver uses:
    # factory(record_id_by_key) -> EvidenceMaterializer -> bind onto the QA stage -> treatment.
    fx = load_fixture()
    rec = _RecordingCompletion()
    stage = build_b2_qa_stage(completion_fn=rec)  # CONTROL by default
    assert stage.evidence_materializer is None

    fetcher = _fake_fetcher_over_fixture(fx)

    def factory(record_id_by_key):
        return build_runtime_evidence_materializer(
            record_id_by_key, source_text_fetcher=fetcher
        )

    # what RealB1Driver.run calls after corpus provisioning:
    bound = bind_treatment_materializer(stage, factory, _fake_record_id_by_key(fx))
    assert bound is True
    assert stage.evidence_materializer is not None

    ordered, vres, gres = _all_query_results(fx)
    asyncio.run(stage(fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres))
    assert len(rec.prompts) == 72
    assert all("[Source:" in p for p in rec.prompts)
    assert any(fx.source_text("A1") in p for p in rec.prompts)


def test_treatment_executable_with_existing_runtime_surface_no_code_edit():
    # A future authorized turn only passes runtime args to ALREADY-implemented surfaces.
    import inspect

    from open_notebook.integrations.graphrag.eval.driver_live_pn02d import RealB1Driver
    from open_notebook.integrations.graphrag.eval.p1diagrunnerpn02db3 import (
        run_live_b3_observability_execution,
    )
    from open_notebook.integrations.graphrag.eval.realseamsb2pn02d import (
        _b2_driver_kwargs,
        run_live_b2_execution,
    )

    assert "treatment_materialization" in inspect.signature(run_live_b2_execution).parameters
    assert (
        "treatment_materialization"
        in inspect.signature(run_live_b3_observability_execution).parameters
    )
    assert (
        "evidence_materializer_factory"
        in inspect.signature(RealB1Driver.__init__).parameters
    )
    # treatment=True threads the POST-PROVISION runtime factory to the driver kwargs
    kw = _b2_driver_kwargs(
        cast(Any, object()),
        evidence_materializer_factory=build_runtime_evidence_materializer,
    )
    assert kw["evidence_materializer_factory"] is build_runtime_evidence_materializer


def test_governed_entrypoint_default_is_control():
    import inspect

    from open_notebook.integrations.graphrag.eval.p1diagrunnerpn02db3 import (
        run_live_b3_observability_execution,
    )
    from open_notebook.integrations.graphrag.eval.realseamsb2pn02d import (
        _b2_driver_kwargs,
        run_live_b2_execution,
    )

    # default treatment_materialization is False on both governed entrypoints
    assert (
        inspect.signature(run_live_b2_execution).parameters["treatment_materialization"].default
        is False
    )
    assert (
        inspect.signature(run_live_b3_observability_execution)
        .parameters["treatment_materialization"]
        .default
        is False
    )
    # default driver kwargs carry no factory (CONTROL)
    kw = _b2_driver_kwargs(cast(Any, object()))
    assert kw["evidence_materializer_factory"] is None


def test_governed_entrypoint_treatment_explicit_only_cli_default_off():
    from open_notebook.integrations.graphrag.eval import cli_live_pn02d as cli

    parser = cli.build_parser()
    ns_default = parser.parse_args(["execute-b3-observability-live", "--manifest", "m.json"])
    assert ns_default.treatment is False  # ordinary run = CONTROL
    ns_treat = parser.parse_args(
        ["execute-b3-observability-live", "--manifest", "m.json", "--treatment", "--authorize"]
    )
    assert ns_treat.treatment is True  # explicit opt-in only


# --------------------------------------------------------------------------- #
# Optional hardening (PN02D-B3P-R1 §32/§33): serialized artifact + failure-path logs
# --------------------------------------------------------------------------- #

def test_serialized_b3_artifact_has_no_raw_source_content():
    import json as _json

    from open_notebook.integrations.graphrag.eval.p1diagrunnerpn02db3 import (
        B3ObservabilityCollector,
        build_b3_observability_artifact,
    )

    fx = load_fixture()
    collector = B3ObservabilityCollector()
    stage = B2QAStage(
        answer_seam=RealFinalAnswerSeam(completion_fn=_RecordingCompletion()),
        evidence_materializer=EvidenceMaterializer(resolver=_make_fixture_resolver(fx)),
        execution_observer=collector,
    )
    ordered, vres, gres = _all_query_results(fx)
    asyncio.run(stage(fx=fx, ordered_queries=ordered, vector_results=vres, gd_results=gres))
    artifact = build_b3_observability_artifact(
        fx, collector.records, observation_run_id="pn02db3p-r1-test-not-a-real-run"
    )
    serialized = _json.dumps(artifact)
    # the actual SERIALIZED artifact must contain no raw source text (only ids/labels/counts)
    for key in fx.source_keys:
        assert fx.source_text(key) not in serialized


def test_failure_path_materialization_error_logs_no_raw_content(caplog):
    # A fail-closed materialization error under caplog must not leak raw source content.
    async def resolver(nb, sid):
        return "SECRET_TAIL_TEXT" if sid == "A" else None  # "B" missing -> fail closed

    m = EvidenceMaterializer(resolver=resolver)
    with caplog.at_level("DEBUG"):
        with pytest.raises(EvidenceMaterializationError):
            asyncio.run(
                m.materialize("NB", ["A", "B"], allowed_source_ids=frozenset({"A", "B"}))
            )
    assert "SECRET_TAIL_TEXT" not in caplog.text
