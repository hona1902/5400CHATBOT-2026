"""GraphRAG-PN02 fixture validation + hash freeze tests (task §48, §14, §54, §63).

OFFLINE — no DB, no provider, no network. Enforces the frozen shape, the content
consistency gates that must hold BEFORE any provider-backed run, deterministic
canonical hashing, and the freeze/mutation guard.
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval import datasetpn02 as d


@pytest.fixture(scope="module")
def fx() -> d.FixturePN02:
    return d.load_fixture()


def test_loads_and_validates(fx: d.FixturePN02) -> None:
    d.validate_fixture(fx)  # raises on any deviation


def test_frozen_counts(fx: d.FixturePN02) -> None:
    assert len(fx.notebooks) == d.FROZEN_NOTEBOOK_COUNT == 3
    assert len(fx.sources) == d.FROZEN_CANONICAL_SOURCE_COUNT == 21
    assert len(fx.unique_source_keys()) == d.FROZEN_UNIQUE_SOURCE_COUNT == 18
    assert len(fx.shared_source_keys()) == d.FROZEN_SHARED_SOURCE_COUNT == 3
    assert len(fx.memberships) == d.FROZEN_WORKSPACE_MEMBERSHIP_COUNT == 24
    assert len(fx.queries) == d.FROZEN_QUERY_COUNT == 24


def test_canonical_not_duplicated_records(fx: d.FixturePN02) -> None:
    # 21 canonical Sources, NOT 24 duplicated canonical records (review question B).
    keys = [s.key for s in fx.sources]
    assert len(keys) == len(set(keys)) == 21
    # Shared Sources are ONE canonical record each, appearing in two memberships.
    for sk in fx.shared_source_keys():
        assert len(fx.notebooks_of(sk)) == 2


def test_eight_sources_per_notebook(fx: d.FixturePN02) -> None:
    for nb in fx.notebook_ids:
        assert len(fx.members_of(nb)) == d.FROZEN_SOURCES_PER_NOTEBOOK == 8


def test_shared_source_memberships(fx: d.FixturePN02) -> None:
    assert fx.notebooks_of("SH_AB") == frozenset({"NB_A", "NB_B"})
    assert fx.notebooks_of("SH_AC") == frozenset({"NB_A", "NB_C"})
    assert fx.notebooks_of("SH_BC") == frozenset({"NB_B", "NB_C"})


def test_eight_classes_one_per_notebook(fx: d.FixturePN02) -> None:
    classes = {q.query_class for q in fx.queries}
    assert len(classes) == 8
    for nb in fx.notebook_ids:
        nb_classes = [q.query_class for q in fx.queries_for_notebook(nb)]
        assert len(nb_classes) == 8
        assert len(set(nb_classes)) == 8  # exactly one per class
    for qc in d.QueryClassPN02:
        assert sum(1 for q in fx.queries if q.query_class is qc) == 3


def test_three_negatives_three_multihop(fx: d.FixturePN02) -> None:
    assert sum(1 for q in fx.queries if q.is_negative) == 3
    assert sum(1 for q in fx.queries if q.multi_hop_required) == 3
    # Every multi-hop has >=2 required (R1) and is genuinely multi-source.
    for q in fx.queries:
        if q.multi_hop_required:
            assert len(q.required_source_ids) >= 2


def test_negatives_have_empty_required_and_abstain(fx: d.FixturePN02) -> None:
    for q in fx.queries:
        if q.is_negative:
            assert q.required_source_ids == ()
            assert q.expected_abstention is True
            assert q.expected_answer_facts == ()


def test_ground_truth_membership_consistency(fx: d.FixturePN02) -> None:
    # REQUIRED/OPTIONAL/citations are members; FORBIDDEN are non-members;
    # no source is both required and forbidden, or optional and forbidden.
    for q in fx.queries:
        members = fx.members_of(q.notebook_id)
        assert set(q.required_source_ids) <= members
        assert set(q.optional_support_source_ids) <= members
        assert set(q.required_citation_source_ids) <= members
        assert set(q.forbidden_source_ids).isdisjoint(members)
        assert set(q.required_source_ids).isdisjoint(q.forbidden_source_ids)
        assert set(q.optional_support_source_ids).isdisjoint(q.forbidden_source_ids)
        assert set(q.required_source_ids).isdisjoint(q.optional_support_source_ids)


def test_collision_queries_forbid_counterparts(fx: d.FixturePN02) -> None:
    # Every CROSS_NOTEBOOK_COLLISION query forbids the specific collision sources.
    coll = [q for q in fx.queries if q.query_class is d.QueryClassPN02.CROSS_NOTEBOOK_COLLISION]
    assert len(coll) == 3
    for q in coll:
        assert len(q.forbidden_source_ids) >= 1
        assert len(q.forbidden_answer_facts) >= 1


def test_forbidden_facts_only_in_non_members(fx: d.FixturePN02) -> None:
    for q in fx.queries:
        member_text = " ".join(fx.source_text(k) for k in fx.members_of(q.notebook_id))
        for token in q.forbidden_answer_facts:
            assert token not in member_text


def test_hash_is_deterministic(fx: d.FixturePN02) -> None:
    h1 = d.compute_fixture_hash(fx)
    h2 = d.compute_fixture_hash(d.load_fixture())
    assert h1 == h2 and len(h1) == 64


def test_frozen_hash_matches(fx: d.FixturePN02) -> None:
    ok, detail = d.verify_fixture_hash()
    assert ok, detail
    freeze = d.load_freeze()
    assert freeze["fixture_sha256"] == d.compute_fixture_hash(fx)


def test_mutation_changes_hash(fx: d.FixturePN02) -> None:
    # Any content change must alter the canonical hash (task §64).
    baseline = d.compute_fixture_hash(fx)
    mutated_sources = list(fx.sources)
    mutated_sources[0] = d.SourcePN02(
        key=mutated_sources[0].key,
        title=mutated_sources[0].title,
        text=mutated_sources[0].text + " EXTRA",
    )
    mutated = d.FixturePN02(
        fixture_version=fx.fixture_version,
        namespace_tag=fx.namespace_tag,
        notebooks=fx.notebooks,
        sources=tuple(mutated_sources),
        memberships=fx.memberships,
        queries=fx.queries,
    )
    assert d.compute_fixture_hash(mutated) != baseline


def test_no_runtime_record_ids_as_source_keys(fx: d.FixturePN02) -> None:
    for s in fx.sources:
        assert ":" not in s.key
        assert not s.key.startswith("source:")


def test_validator_rejects_leaky_forbidden_membership() -> None:
    # A member Source can never be forbidden.
    fx = d.load_fixture()
    bad_q = d.QueryPN02(
        query_id="BAD",
        notebook_id="NB_A",
        query_class=d.QueryClassPN02.DIRECT_LOCAL,
        question="q",
        answerable=True,
        required_source_ids=("A1",),
        optional_support_source_ids=(),
        forbidden_source_ids=("A2",),  # A2 IS a member of NB_A -> invalid
        expected_answer_facts=("AX-17",),
        forbidden_answer_facts=(),
        expected_abstention=False,
        required_citation_source_ids=("A1",),
        multi_hop_required=False,
        rationale="bad",
    )
    broken = d.FixturePN02(
        fixture_version=fx.fixture_version,
        namespace_tag=fx.namespace_tag,
        notebooks=fx.notebooks,
        sources=fx.sources,
        memberships=fx.memberships,
        queries=fx.queries + (bad_q,),
    )
    with pytest.raises(d.FixturePN02Error):
        d.validate_fixture(broken)


def test_removal_scenario_derivation(fx: d.FixturePN02) -> None:
    scen = d.membership_removal_scenario(fx)
    assert scen.shared_source == "SH_AB"
    assert scen.removed_from_notebook == "NB_A"
    assert scen.retained_notebook == "NB_B"
    assert "SH_AB" not in scen.members_after_removed_nb
    assert "SH_AB" in scen.members_after_retained_nb
    assert len(scen.reprobe_query_ids) == 2
