"""GraphRAG-PN02 QA-V+GD combination rule tests (task §31; PN02A §10b).

OFFLINE. Deduplication, membership filtering, cap, canonical-id ordering of GD
additions (no fake rank/score), and forbidden-source exclusion.
"""

from __future__ import annotations

from open_notebook.integrations.graphrag.eval.combinepn02 import (
    K5_CAP,
    combine_v_gd,
)

MEMBERS = frozenset({"A1", "A2", "A3", "A4", "A5", "A6", "SH_AB", "SH_AC"})


def test_v_order_preserved_gd_appended_by_id() -> None:
    v5 = ["A3", "A1", "A5"]           # a genuine vector rank
    gd = {"A6", "A2", "A4"}           # unordered set
    out = combine_v_gd(v5, gd, MEMBERS)
    # V portion keeps rank; GD additions sorted by canonical id (NOT any score).
    assert out.v5_member_portion == ("A3", "A1", "A5")
    assert out.gd_added == ("A2", "A4", "A6")
    assert out.ordered_source_ids == ("A3", "A1", "A5", "A2", "A4", "A6")


def test_deduplication() -> None:
    v5 = ["A1", "A1", "A2"]           # duplicate in V
    gd = {"A2", "A3"}                 # A2 already in V
    out = combine_v_gd(v5, gd, MEMBERS)
    assert out.ordered_source_ids == ("A1", "A2", "A3")
    assert out.gd_added == ("A3",)


def test_non_members_dropped_from_both() -> None:
    v5 = ["A1", "B3"]                 # B3 is a non-member
    gd = {"C2", "A2"}                 # C2 is a non-member
    out = combine_v_gd(v5, gd, MEMBERS)
    assert "B3" not in out.ordered_source_ids
    assert "C2" not in out.ordered_source_ids
    assert set(out.dropped_non_members) == {"B3", "C2"}
    assert out.ordered_source_ids == ("A1", "A2")


def test_forbidden_source_excluded() -> None:
    # A forbidden collision source (B2) must never survive the combination.
    out = combine_v_gd(["A2"], {"B2"}, MEMBERS)
    assert "B2" not in out.ordered_source_ids
    assert out.gd_added == ()


def test_cap_at_notebook_size() -> None:
    assert K5_CAP == 8
    v5 = ["A1", "A2", "A3", "A4", "A5"]
    gd = {"A6", "SH_AB", "SH_AC"}     # 3 additions -> 8 total, exactly at cap
    out = combine_v_gd(v5, gd, MEMBERS)
    assert len(out.ordered_source_ids) == 8
    assert out.capped is False
    # Adding a 9th distinct member is impossible (only 8 members), but the cap is
    # still enforced structurally with a small cap.
    out2 = combine_v_gd(v5, gd, MEMBERS, cap=6)
    assert len(out2.ordered_source_ids) == 6
    assert out2.capped is True


def test_all_admitted_sources_are_members() -> None:
    out = combine_v_gd(["A1", "A9nonmember"], {"A2", "ZZ"}, MEMBERS)
    assert set(out.ordered_source_ids) <= MEMBERS
