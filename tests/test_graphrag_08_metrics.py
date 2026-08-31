"""GraphRAG-08 metric unit tests (offline; no DB, no provider, no network).

Covers task §53 (no fake rank — permutation invariance), §54 (broad-candidate
gaming cannot masquerade as quality), §55 (negatives never success), and the
frozen set/breadth/complementarity formulas (§60, §63-§65).
"""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval import metrics08 as m


def _fs(*ids: str):
    return frozenset(ids)


# -- basic set formulas ------------------------------------------------------

def test_set_recall_precision_f1_basic():
    required = _fs("A", "B")
    cands = _fs("A", "B", "X")
    assert m.set_recall(cands, required) == 1.0
    assert m.set_precision(cands, required) == pytest.approx(2 / 3)
    assert m.set_f1(cands, required) == pytest.approx(2 * (2 / 3) * 1.0 / (2 / 3 + 1.0))


def test_set_precision_none_when_no_candidates():
    assert m.set_precision(frozenset(), _fs("A")) is None
    assert m.set_f1(frozenset(), _fs("A")) is None


def test_set_metrics_raise_on_empty_required():
    for fn in (m.set_recall, m.set_precision, m.set_f1):
        with pytest.raises(ValueError):
            fn(_fs("A"), frozenset())
    with pytest.raises(ValueError):
        m.full_source_set_recovered(_fs("A"), frozenset())


# -- permutation invariance (task §53) --------------------------------------

def test_graph_metrics_are_order_free():
    required = _fs("A", "B")
    seq1 = ["A", "B", "C", "D"]
    seq2 = ["D", "C", "B", "A"]
    s1, s2 = frozenset(seq1), frozenset(seq2)
    assert s1 == s2
    # Every graph metric is a pure function of the SET, so both orders agree.
    assert m.set_recall(s1, required) == m.set_recall(s2, required)
    assert m.set_precision(s1, required) == m.set_precision(s2, required)
    assert m.set_f1(s1, required) == m.set_f1(s2, required)
    assert m.candidate_count(s1) == m.candidate_count(s2)
    assert m.candidate_fraction(s1, 10) == m.candidate_fraction(s2, 10)
    assert m.full_source_set_recovered(s1, required) == m.full_source_set_recovered(
        s2, required
    )


# -- broad-candidate gaming cannot look like quality (task §54) --------------

def test_broad_candidate_high_recall_low_precision_high_fraction():
    corpus = 8
    required = _fs("A")  # single relevant source
    returned = _fs("A", "B", "C", "D", "E", "F", "G")  # 7 of 8 sources
    assert m.set_recall(returned, required) == 1.0  # perfect recall...
    precision = m.set_precision(returned, required)
    fraction = m.candidate_fraction(returned, corpus)
    assert precision is not None and precision < 0.2  # ...but terrible precision
    assert fraction > 0.8  # and it flagged most of the corpus
    assert m.false_positive_count(returned, required) == 6


# -- multi-source full vs partial recovery (task §62) -----------------------

def test_full_and_partial_recovery():
    required = _fs("A", "B")
    assert m.full_source_set_recovered(_fs("A", "B", "C"), required) is True
    assert m.partial_source_set_recovered(_fs("A", "B", "C"), required) is False
    assert m.full_source_set_recovered(_fs("A"), required) is False
    assert m.partial_source_set_recovered(_fs("A"), required) is True
    assert m.partial_source_set_recovered(_fs("Z"), required) is False


# -- false positives exclude optional support (task §17) --------------------

def test_optional_support_is_not_a_false_positive():
    required = _fs("A")
    optional = _fs("B")
    allowed = required | optional
    cands = _fs("A", "B", "X")  # B is optional-support, X is a real FP
    assert m.false_positive_count(cands, required, allowed=allowed) == 1


# -- negative metrics never treat output as success (task §41, §55) ---------

def test_negative_abstention_and_false_positives():
    assert m.abstained(frozenset()) is True
    assert m.abstained(_fs("A")) is False
    # For a negative (empty required, empty allowed) every candidate is a FP.
    assert m.false_positive_count(_fs("A", "B"), frozenset(), allowed=frozenset()) == 2


# -- breadth distribution (task §26, §42) -----------------------------------

def test_breadth_distribution():
    dist = m.breadth_distribution([1, 2, 3, 4, 100]).as_dict()
    assert dist["n"] == 5
    assert dist["max"] == 100
    assert dist["median"] == 3
    assert dist["mean"] == pytest.approx(22.0)


def test_breadth_distribution_empty():
    dist = m.breadth_distribution([]).as_dict()
    assert dist["n"] == 0 and dist["median"] is None


# -- FULL-recovery complementarity + oracle union (task §63, §64) -----------

def test_complementarity_full_vector_only():
    required = _fs("A", "B")
    vector_ranked = ["A", "B", "C"]  # vector recovers full at k>=2
    graph_set = _fs("A")  # graph partial only
    cell = m.complementarity_full(vector_ranked, graph_set, required, k=3)
    assert cell.vector_full is True
    assert cell.graph_full is False
    assert cell.vector_only_full is True
    assert cell.graph_only_full is False
    assert cell.oracle_union_full is True


def test_complementarity_full_graph_only_and_oracle():
    required = _fs("A", "B")
    vector_ranked = ["A", "C"]  # vector misses B within budget
    graph_set = _fs("A", "B")  # graph recovers full
    cell = m.complementarity_full(vector_ranked, graph_set, required, k=2)
    assert cell.graph_only_full is True
    assert cell.vector_full is False
    assert cell.oracle_union_full is True


def test_candidate_fraction_denominator_validation():
    with pytest.raises(ValueError):
        m.candidate_fraction(_fs("A"), 0)
