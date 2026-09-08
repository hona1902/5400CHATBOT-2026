"""PN02D-B0B — stateful pre-op budget guard (design §21-§23/§26/§58)."""

from __future__ import annotations

import pytest

from open_notebook.integrations.graphrag.eval.budgetlivepn02d import (
    BudgetClass,
    StatefulBudgetGuard,
    WorkloadCapExceeded,
    b1_caps,
)


def test_caps_match_frozen_design():
    caps = b1_caps()
    assert caps[BudgetClass.GRAPH_INDEX_OPERATION] == 24
    assert caps[BudgetClass.GRAPH_INDEX_ATTEMPT] == 48
    assert caps[BudgetClass.GRAPH_DELETE] == 1
    assert caps[BudgetClass.GD_QUERY] == 26
    assert caps[BudgetClass.VECTOR_QUERY] == 26
    assert caps[BudgetClass.QUERY_EMBEDDING] == 26
    # B1 override: final answer / client.query / judge are HARD 0.
    assert caps[BudgetClass.FINAL_ANSWER] == 0
    assert caps[BudgetClass.CLIENT_QUERY] == 0
    assert caps[BudgetClass.JUDGE_MODEL] == 0


@pytest.mark.parametrize(
    "cls",
    [BudgetClass.FINAL_ANSWER, BudgetClass.CLIENT_QUERY, BudgetClass.JUDGE_MODEL],
)
def test_hard_zero_classes_refuse_first_reservation(cls):
    g = StatefulBudgetGuard()
    with pytest.raises(WorkloadCapExceeded):
        g.reserve(cls)
    assert g.spent(cls) == 0  # no counter mutation on refusal


def test_reserve_up_to_cap_then_refuse_before_op():
    g = StatefulBudgetGuard()
    for _ in range(26):
        g.reserve(BudgetClass.GD_QUERY)
    assert g.spent(BudgetClass.GD_QUERY) == 26
    with pytest.raises(WorkloadCapExceeded):
        g.reserve(BudgetClass.GD_QUERY)  # 27th refused BEFORE the op
    assert g.spent(BudgetClass.GD_QUERY) == 26  # unchanged


def test_index_attempt_cap_48():
    g = StatefulBudgetGuard()
    for _ in range(48):
        g.reserve(BudgetClass.GRAPH_INDEX_ATTEMPT)
    with pytest.raises(WorkloadCapExceeded):
        g.reserve(BudgetClass.GRAPH_INDEX_ATTEMPT)


def test_snapshot_is_content_safe():
    g = StatefulBudgetGuard()
    g.reserve(BudgetClass.VECTOR_QUERY)
    snap = g.snapshot()
    assert snap[BudgetClass.VECTOR_QUERY.value] == {"spent": 1, "cap": 26}
    assert set(snap) == {c.value for c in BudgetClass}
