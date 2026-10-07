"""GraphRAG-09H: durable evidence for the pn02_slice_v1 dataset slice hash.

Provider-free, no DB, no network. Pins the historically-approved derivation of
the NB_A / A1,A2,A3 09H index slice hash so it can never again live only in a
session transcript:

    PN02_SLICE_V1_SHA256 = 0d57df2188126f4a4dfefa51c98af5455e55a81e04a36af68f459a6c1bc3142c

The slice reuses the same canonicalization as compute_fixture_hash (sorted-key
compact JSON, ensure_ascii, UTF-8, SHA-256) and binds the parent fixture hash.
"""

import dataclasses

from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    PN02_SLICE_V1_ALGORITHM_ID,
    PN02_SLICE_V1_CANONICAL_BYTE_LENGTH,
    PN02_SLICE_V1_SOURCE_KEYS,
    compute_fixture_hash,
    compute_slice_hash,
    load_fixture,
    pn02_slice_v1_canonical_bytes,
    pn02_slice_v1_descriptor,
)

PARENT_SHA256 = "9ce7df742810424d9ef7b7b34962187d9061245f81471f5fb1d7f2608f6899a6"
SLICE_SHA256 = "0d57df2188126f4a4dfefa51c98af5455e55a81e04a36af68f459a6c1bc3142c"


# ---------------------------------------------------------------- golden hash
def test_golden_slice_hash_matches_historical():
    assert compute_slice_hash() == SLICE_SHA256


def test_canonical_byte_length_is_754():
    fx = load_fixture()
    assert len(pn02_slice_v1_canonical_bytes(fx)) == PN02_SLICE_V1_CANONICAL_BYTE_LENGTH
    assert PN02_SLICE_V1_CANONICAL_BYTE_LENGTH == 754


def test_parent_binding():
    fx = load_fixture()
    assert compute_fixture_hash(fx) == PARENT_SHA256
    desc = pn02_slice_v1_descriptor(fx, compute_fixture_hash(fx))
    assert desc["parent_fixture_sha256"] == PARENT_SHA256
    # the slice is bound to the parent-verified fixture
    assert compute_slice_hash() == SLICE_SHA256


def test_algorithm_metadata():
    assert PN02_SLICE_V1_ALGORITHM_ID == "pn02_slice_v1"
    assert tuple(PN02_SLICE_V1_SOURCE_KEYS) == ("A1", "A2", "A3")


def test_source_order_is_fixed_a1_a2_a3():
    fx = load_fixture()
    desc = pn02_slice_v1_descriptor(fx, PARENT_SHA256)
    assert [s["key"] for s in desc["sources"]] == ["A1", "A2", "A3"]
    assert desc["memberships"] == [["A1", "NB_A"], ["A2", "NB_A"], ["A3", "NB_A"]]


# ---------------------------------------------------------------- sensitivity
def _fixture_with_source_text(fx, key, new_text):
    sources = tuple(
        dataclasses.replace(s, text=new_text) if s.key == key else s
        for s in fx.sources
    )
    return dataclasses.replace(fx, sources=sources)


def _fixture_with_notebook_record_id(fx, notebook_id, new_record_id):
    notebooks = tuple(
        dataclasses.replace(n, record_id=new_record_id) if n.notebook_id == notebook_id else n
        for n in fx.notebooks
    )
    return dataclasses.replace(fx, notebooks=notebooks)


def test_content_sensitive():
    fx = load_fixture()
    mutated = _fixture_with_source_text(fx, "A1", "DIFFERENT TEXT")
    assert pn02_slice_v1_canonical_bytes(mutated, PARENT_SHA256) != pn02_slice_v1_canonical_bytes(
        fx, PARENT_SHA256
    )


def test_membership_sensitive():
    # Alternate descriptor over a different source combination (A1,A2,A4).
    fx = load_fixture()
    base = pn02_slice_v1_descriptor(fx, PARENT_SHA256)
    alt = pn02_slice_v1_descriptor(fx, PARENT_SHA256, source_keys=("A1", "A2", "A4"))
    assert alt != base
    import hashlib
    import json

    def sha(d):
        return hashlib.sha256(
            json.dumps(d, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()

    assert sha(alt) != SLICE_SHA256


def test_identity_sensitive():
    fx = load_fixture()
    mutated = _fixture_with_notebook_record_id(fx, "NB_A", "notebook:gr_pn02_b")
    assert pn02_slice_v1_canonical_bytes(mutated, PARENT_SHA256) != pn02_slice_v1_canonical_bytes(
        fx, PARENT_SHA256
    )


def test_parent_binding_is_sensitive():
    fx = load_fixture()
    a = pn02_slice_v1_canonical_bytes(fx, PARENT_SHA256)
    b = pn02_slice_v1_canonical_bytes(fx, "0" * 64)
    assert a != b


# ---------------------------------------------------------------- exclusions
def test_query_not_in_slice():
    fx = load_fixture()
    desc = pn02_slice_v1_descriptor(fx, PARENT_SHA256)
    assert "queries" not in desc
    raw = pn02_slice_v1_canonical_bytes(fx, PARENT_SHA256)
    assert b"PN02Q01" not in raw
    assert b"question" not in raw


def test_runtime_mutable_fields_excluded():
    fx = load_fixture()
    desc = pn02_slice_v1_descriptor(fx, PARENT_SHA256)
    assert set(desc.keys()) == {
        "fixture",
        "namespace_tag",
        "parent_fixture_sha256",
        "notebook",
        "sources",
        "memberships",
    }
    # notebook carries only id + record_id (no mutable theme / runtime fields)
    assert set(desc["notebook"].keys()) == {"notebook_id", "record_id"}
    raw = pn02_slice_v1_canonical_bytes(fx, PARENT_SHA256)
    for forbidden in (b"doc-", b"workspace", b"nb_2801282a", b"chat_session", b"model:gr_pn02", b"created", b"updated", b"timestamp", b"theme"):
        assert forbidden not in raw


# ---------------------------------------------------------------- stability
def test_platform_stable_sort_keys():
    import hashlib
    import json

    fx = load_fixture()
    desc = pn02_slice_v1_descriptor(fx, PARENT_SHA256)
    reordered = {k: desc[k] for k in reversed(list(desc.keys()))}
    h1 = hashlib.sha256(
        json.dumps(desc, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    h2 = hashlib.sha256(
        json.dumps(reordered, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert h1 == h2 == SLICE_SHA256
