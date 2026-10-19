"""GraphRAG-09H Run #6 — provenance expected-contract regression (provider-free, no DB, no sidecar).

Reproduces the exact Run #6 final-provenance mismatch deterministically and pins the corrected
contract. It drives the REAL ``build_chat_graph_provenance`` + REAL 09E ``validate_graph_references``
against an in-memory canonical authority (the same idiom as test_graphrag_09f_chat_provenance.py),
with:

  * the frozen A1+A2 canonical chat context (``CANONICAL_CONTEXT_SOURCE_IDS``);
  * the frozen indexed corpus {A1, A2, A3} as graph candidate references (Run #6's real graph query
    returned 3 references = the 3 indexed docs), all live members of the frozen notebook.

09F provenance is a CONTEXT-MEMBERSHIP ∩ GRAPH-VALIDATION marker and is ANSWER-TEXT-INDEPENDENT
(the helper never receives the answer), so the derived expectation is a property of the frozen
inputs, not of any single stochastic chat answer. The deterministic output is (A1, A2) in canonical
context order; A3 is validated but dropped (not in context). This equals the corrected
``EXPECTED_PRODUCT_PROVENANCE`` and would FAIL under the old ("source:gr_pn02_a2",) assumption.

No provider/network/DB/sidecar traffic: the graph query is injected and validation runs against an
in-memory authority whose membership facts were independently confirmed against the frozen DB.
"""

import pytest

from open_notebook.database.repository import ensure_record_id
from open_notebook.integrations.graphrag.eval import productlivepn02d as plo
from open_notebook.integrations.graphrag.models import (
    _INDEXABLE_TABLES,
    GraphReference,
    record_id_for,
)
from open_notebook.integrations.graphrag.product_provenance import (
    build_chat_graph_provenance,
)
from open_notebook.integrations.graphrag.provenance import validate_graph_references

A1, A2, A3 = plo.INDEX_SOURCE_IDS  # source:gr_pn02_a1 / a2 / a3
NB = plo.NOTEBOOK_ID
CONTEXT = list(plo.CANONICAL_CONTEXT_SOURCE_IDS)  # [A1, A2]
OLD_MISSPECIFIED_EXPECTATION = ("source:gr_pn02_a2",)  # the Run #6 test-injected value


def _skey(source_id: str) -> str:
    return str(record_id_for(source_id, tables=_INDEXABLE_TABLES))


def _nkey(notebook_id: str) -> str:
    return str(ensure_record_id(notebook_id))


def _ref(source_id: str) -> GraphReference:
    return GraphReference(
        source_id=source_id, reference_id="ref-should-not-surface", resolved=True, excerpts=[]
    )


class _FrozenCanonical:
    """In-memory canonical authority mirroring the shape the real 09E validator queries.

    Models the frozen notebook: A1, A2, A3 are all live sources AND members of NB (independently
    confirmed read-only against the real SurrealDB during the amendment).
    """

    def __init__(self) -> None:
        self.live = {_skey(s): None for s in (A1, A2, A3)}
        self.memberships = {(_nkey(NB), _skey(s)) for s in (A1, A2, A3)}

    async def repo_query(self, sql: str, params: dict | None = None):
        params = params or {}
        if "FROM reference" in sql:
            nb = str(params["notebook"])
            ids = [str(x) for x in params["ids"]]
            return [{"in": sid} for sid in ids if (nb, sid) in self.memberships]
        if "FROM source" in sql:
            ids = [str(x) for x in params["ids"]]
            return [{"id": sid, "title": self.live[sid]} for sid in ids if sid in self.live]
        raise AssertionError(f"unexpected query: {sql}")


class _Corpus3Query:
    """Injected graph query returning the frozen indexed corpus {A1, A2, A3}. No provider."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, question):
        self.calls += 1
        return [_ref(A1), _ref(A2), _ref(A3)]


@pytest.fixture
def enabled_env(monkeypatch):
    """Feature ON so load_config().configured is True; base_url never contacted (query injected)."""
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_BASE_URL", "http://sidecar.invalid:9999")
    monkeypatch.delenv("OPEN_NOTEBOOK_GRAPHRAG_API_KEY", raising=False)


async def _real_09f_once():
    fake = _FrozenCanonical()
    query = _Corpus3Query()
    result = await build_chat_graph_provenance(
        question="What calibration profile is the Aurora sensor array calibrated with?",
        notebook_id=NB,
        canonical_source_ids=CONTEXT,
        graph_query=query,
        validate=validate_graph_references,  # REAL 09E validator
        repo_query=fake.repo_query,
    )
    assert query.calls == 1  # at most one graph query
    return result


@pytest.mark.asyncio
async def test_deterministic_09f_output_is_a1_a2(enabled_env):
    """Real 09F over the frozen corpus+context deterministically yields (A1, A2), A3 dropped."""
    r1 = await _real_09f_once()
    r2 = await _real_09f_once()
    assert r1 is not None and r2 is not None
    assert r1.source_ids == r2.source_ids  # deterministic across independent recomputations
    assert r1.source_ids == (_skey(A1), _skey(A2))  # context order, A3 absent
    assert _skey(A3) not in r1.source_ids
    assert r1.count == 2
    assert r1.has_validated_graph_provenance is True


@pytest.mark.asyncio
async def test_corrected_contract_matches_real_09f(enabled_env):
    """The corrected EXPECTED_PRODUCT_PROVENANCE equals the deterministic real-09F output.

    This ties the contract to actual 09F behavior and FAILS under the old ("source:gr_pn02_a2",)
    assumption (pre-amendment this assertion does not hold).
    """
    result = await _real_09f_once()
    assert result is not None
    assert tuple(result.source_ids) == plo.EXPECTED_PRODUCT_PROVENANCE
    assert plo.EXPECTED_PRODUCT_PROVENANCE == ("source:gr_pn02_a1", "source:gr_pn02_a2")
    assert plo.EXPECTED_PRODUCT_PROVENANCE != OLD_MISSPECIFIED_EXPECTATION  # regression guard


@pytest.mark.asyncio
async def test_run6_final_provenance_invariant_passes(enabled_env):
    """The exact kill-switch comparison (ordered list-equality) no longer trips for Run #6 output."""
    result = await _real_09f_once()
    assert result is not None
    final_product_provenance_source_ids = list(result.source_ids)
    # Mirrors _check_product_invariants: final == list(EXPECTED) => no "final_provenance_mismatch".
    assert final_product_provenance_source_ids == list(plo.EXPECTED_PRODUCT_PROVENANCE)
    # And A3 isolation remains strict: the true (isolated) answer source never surfaces on product.
    assert _skey(A3) not in final_product_provenance_source_ids
    assert final_product_provenance_source_ids != list(OLD_MISSPECIFIED_EXPECTATION)
