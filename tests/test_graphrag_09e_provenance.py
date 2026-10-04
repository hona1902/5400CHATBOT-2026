"""GraphRAG-09E citable-provenance validation tests (provider-free, no sidecar, no DB server).

Exercise the REAL validator ``provenance.validate_graph_references`` with synthetic ``GraphReference``
objects and an injected fake repo-query that models the canonical ``source`` table + ``reference``
relation (in=source, out=notebook). The focus is query-time authorization and notebook isolation:
graph output is UNTRUSTED and becomes citable only after canonical existence + membership checks.
"""

import pytest

from open_notebook.database.repository import ensure_record_id
from open_notebook.integrations.graphrag.models import _INDEXABLE_TABLES, record_id_for
from open_notebook.integrations.graphrag.provenance import (
    MAX_REFERENCES,
    ValidatedProvenance,
    validate_graph_references,
)

# A sidecar-produced reference; source_id is only an untrusted identifier hint.
try:
    from open_notebook.integrations.graphrag.models import GraphReference
except Exception:  # pragma: no cover
    GraphReference = None  # type: ignore

SENTINEL = "sk-or-SENTINELSHOULDNEVERSURFACE0123456789"


def _ref(source_id, *, excerpts=None, resolved=True):
    return GraphReference(
        source_id=source_id,
        reference_id=None,
        resolved=resolved,
        excerpts=list(excerpts or []),
    )


def _skey(source_id: str) -> str:
    """Canonical key exactly as the validator computes it (str of the built RecordID)."""
    return str(record_id_for(source_id, tables=_INDEXABLE_TABLES))


def _nkey(notebook_id: str) -> str:
    return str(ensure_record_id(notebook_id))


class FakeCanonical:
    """In-memory canonical authority: live sources (+title) and reference membership edges."""

    def __init__(self) -> None:
        self.live: dict[str, str | None] = {}  # str(source_rid) -> title
        self.memberships: set[tuple[str, str]] = set()  # (str(notebook), str(source))
        self.calls = {"source": 0, "reference": 0}
        self.fail_source = False
        self.fail_reference = False

    def add_source(self, source_id: str, *, title: str | None = None) -> None:
        self.live[_skey(source_id)] = title

    def add_membership(self, source_id: str, notebook_id: str) -> None:
        self.memberships.add((_nkey(notebook_id), _skey(source_id)))

    def remove_membership(self, source_id: str, notebook_id: str) -> None:
        self.memberships.discard((_nkey(notebook_id), _skey(source_id)))

    async def repo_query(self, sql: str, params: dict | None = None):
        params = params or {}
        if "FROM reference" in sql:
            self.calls["reference"] += 1
            if self.fail_reference:
                raise RuntimeError(f"membership query boom {SENTINEL}")
            nb = str(params["notebook"])
            ids = [str(x) for x in params["ids"]]
            return [{"in": sid} for sid in ids if (nb, sid) in self.memberships]
        if "FROM source" in sql:
            self.calls["source"] += 1
            if self.fail_source:
                raise RuntimeError(f"source query boom {SENTINEL}")
            ids = [str(x) for x in params["ids"]]
            return [{"id": sid, "title": self.live[sid]} for sid in ids if sid in self.live]
        raise AssertionError(f"unexpected query: {sql}")


def _never_called_repo():
    async def _rq(sql, params=None):  # pragma: no cover - must never run
        raise AssertionError("no DB query expected for pre-DB rejection")
    return _rq


# --------------------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_live_member_reference_validates():
    fake = FakeCanonical()
    fake.add_source("source:s1", title="Doc One")
    fake.add_membership("source:s1", "notebook:a")

    res = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    assert res.refs_validated == 1
    assert len(res.validated) == 1
    p = res.validated[0]
    assert isinstance(p, ValidatedProvenance)
    assert p.canonical_source_id == _skey("source:s1")
    assert p.notebook_id == _nkey("notebook:a")
    assert p.title == "Doc One"
    assert p.rank == 0


# --------------------------------------------------------------------------- rejections


@pytest.mark.asyncio
async def test_foreign_notebook_reference_rejected():
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:b")  # only B

    res = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    assert res.validated == ()
    assert res.rejected_not_member == 1


@pytest.mark.asyncio
async def test_deleted_source_reference_rejected():
    fake = FakeCanonical()  # source not added => absent
    fake.add_membership("source:s1", "notebook:a")  # stale edge in fake membership set

    res = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    assert res.validated == ()
    assert res.rejected_absent == 1


@pytest.mark.asyncio
async def test_removed_membership_reference_rejected():
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:a")
    fake.remove_membership("source:s1", "notebook:a")  # membership removed, source still live

    res = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    assert res.validated == ()
    assert res.rejected_not_member == 1


# ---------------------------------------------------- critical shared-source isolation


@pytest.mark.asyncio
async def test_shared_source_notebook_isolation():
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:a")
    fake.add_membership("source:s1", "notebook:b")

    res_a = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    res_b = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:b", repo_query=fake.repo_query
    )
    assert res_a.refs_validated == 1 and res_b.refs_validated == 1

    # Remove only A's membership; B must remain valid, A must now reject.
    fake.remove_membership("source:s1", "notebook:a")
    res_a2 = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    res_b2 = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:b", repo_query=fake.repo_query
    )
    assert res_a2.validated == () and res_a2.rejected_not_member == 1
    assert res_b2.refs_validated == 1


# --------------------------------------------------------------- structural pre-DB rejects


@pytest.mark.asyncio
async def test_malformed_graph_reference_rejected_pre_db():
    res = await validate_graph_references(
        [_ref(None), _ref("   ")], notebook_id="notebook:a", repo_query=_never_called_repo()
    )
    assert res.validated == ()
    assert res.rejected_malformed == 2


@pytest.mark.asyncio
async def test_invalid_source_id_rejected_pre_db():
    # wrong table + no separator: both structurally non-canonical source ids.
    res = await validate_graph_references(
        [_ref("note:x"), _ref("nocolon")],
        notebook_id="notebook:a",
        repo_query=_never_called_repo(),
    )
    assert res.validated == ()
    assert res.rejected_invalid_id == 2


# --------------------------------------------------------------- identity distinctness / dedup


@pytest.mark.asyncio
async def test_record_id_distinction_preserved():
    numeric = "source:123"
    string_numeric = "source:⟨123⟩"
    assert _skey(numeric) != _skey(string_numeric)  # distinct canonical identities

    fake = FakeCanonical()
    for sid in (numeric, string_numeric):
        fake.add_source(sid)
        fake.add_membership(sid, "notebook:a")

    res = await validate_graph_references(
        [_ref(numeric), _ref(string_numeric)], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    assert res.refs_validated == 2
    keys = {p.canonical_source_id for p in res.validated}
    assert keys == {_skey(numeric), _skey(string_numeric)}  # NOT collapsed by dedup


@pytest.mark.asyncio
async def test_duplicate_reference_dedup():
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:a")

    res = await validate_graph_references(
        [_ref("source:s1"), _ref("source:s1"), _ref("source:s1")],
        notebook_id="notebook:a",
        repo_query=fake.repo_query,
    )
    assert res.refs_validated == 1  # citation weight multiplier = 1
    assert res.duplicates_removed == 2
    assert res.validated[0].rank == 0  # first occurrence kept


# --------------------------------------------------------------- mixed + ordering


@pytest.mark.asyncio
async def test_partial_validation_valid_subset_preserved():
    fake = FakeCanonical()
    fake.add_source("source:ok1")
    fake.add_membership("source:ok1", "notebook:a")
    fake.add_source("source:ok2")
    fake.add_membership("source:ok2", "notebook:a")
    fake.add_source("source:foreign")
    fake.add_membership("source:foreign", "notebook:b")  # foreign to A
    # source:deleted not added => absent

    refs = [
        _ref("source:ok1"),
        _ref("note:bad"),          # invalid id
        _ref("source:deleted"),    # absent
        _ref("source:foreign"),    # not member of A
        _ref(None),                # malformed
        _ref("source:ok2"),
        _ref("source:ok1"),        # duplicate of a valid one
    ]
    res = await validate_graph_references(refs, notebook_id="notebook:a", repo_query=fake.repo_query)
    assert [p.canonical_source_id for p in res.validated] == [_skey("source:ok1"), _skey("source:ok2")]
    assert res.rejected_invalid_id == 1
    assert res.rejected_absent == 1
    assert res.rejected_not_member == 1
    assert res.rejected_malformed == 1
    assert res.duplicates_removed == 1


@pytest.mark.asyncio
async def test_validated_order_preserved():
    fake = FakeCanonical()
    for sid in ("source:s3", "source:s1", "source:s2"):
        fake.add_source(sid)
        fake.add_membership(sid, "notebook:a")

    res = await validate_graph_references(
        [_ref("source:s3"), _ref("source:s1"), _ref("source:s2")],
        notebook_id="notebook:a",
        repo_query=fake.repo_query,
    )
    assert [p.canonical_source_id for p in res.validated] == [
        _skey("source:s3"), _skey("source:s1"), _skey("source:s2")
    ]  # graph order, NOT sorted by id


# --------------------------------------------------------------- fail-closed on DB errors


@pytest.mark.asyncio
async def test_source_lookup_failure_fails_closed():
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:a")
    fake.fail_source = True

    res = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    assert res.validated == ()
    assert res.validation_error == 1
    assert SENTINEL not in repr(res) and SENTINEL not in str(res)


@pytest.mark.asyncio
async def test_membership_lookup_failure_fails_closed():
    fake = FakeCanonical()
    fake.add_source("source:s1")
    fake.add_membership("source:s1", "notebook:a")
    fake.fail_reference = True

    res = await validate_graph_references(
        [_ref("source:s1")], notebook_id="notebook:a", repo_query=fake.repo_query
    )
    assert res.validated == ()
    assert res.validation_error == 1
    assert SENTINEL not in repr(res)


# --------------------------------------------------------------- content safety


@pytest.mark.asyncio
async def test_raw_graph_content_not_carried_to_validated_provenance():
    fake = FakeCanonical()
    fake.add_source("source:s1", title="Clean Title")
    fake.add_membership("source:s1", "notebook:a")

    res = await validate_graph_references(
        [_ref("source:s1", excerpts=[f"leaked {SENTINEL} snippet"])],
        notebook_id="notebook:a",
        repo_query=fake.repo_query,
    )
    assert res.refs_validated == 1
    assert SENTINEL not in repr(res.validated[0])
    assert SENTINEL not in repr(res)


# --------------------------------------------------------------- batched / bounded


@pytest.mark.asyncio
async def test_no_n_plus_one_queries():
    fake = FakeCanonical()
    for i in range(10):
        sid = f"source:s{i}"
        fake.add_source(sid)
        fake.add_membership(sid, "notebook:a")

    res = await validate_graph_references(
        [_ref(f"source:s{i}") for i in range(10)],
        notebook_id="notebook:a",
        repo_query=fake.repo_query,
    )
    assert res.refs_validated == 10
    assert fake.calls["source"] == 1   # exactly one existence query, not N
    assert fake.calls["reference"] == 1  # exactly one membership query, not N


@pytest.mark.asyncio
async def test_provenance_bound_enforced():
    fake = FakeCanonical()
    total = MAX_REFERENCES + 25
    for i in range(total):
        sid = f"source:b{i}"
        fake.add_source(sid)
        fake.add_membership(sid, "notebook:a")

    res = await validate_graph_references(
        [_ref(f"source:b{i}") for i in range(total)],
        notebook_id="notebook:a",
        repo_query=fake.repo_query,
    )
    assert res.refs_received == total
    assert res.refs_considered == MAX_REFERENCES
    assert res.refs_validated == MAX_REFERENCES  # only the bounded prefix
    # deterministic prefix order preserved
    assert [p.rank for p in res.validated] == list(range(MAX_REFERENCES))
