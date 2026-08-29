"""GraphRAG-03B durable deletion state tests.

Property-oriented: each test names the security/lifecycle property that would
break if the implementation were subtly wrong (per the GraphRAG-03B brief).

Two layers:

  * STRUCTURAL (always run, no DB): the migration-24 SQL, the manager
    registration, and the read-only helper source. These pin the shape of the
    tombstone + event and prove the helper does no HTTP/draining.

  * LIVE-DB INTEGRATION (skipped when SurrealDB is unreachable): the event is
    exercised against a real SurrealDB v2 — including a RAW ``DELETE source``
    that runs no Python — because the central confidentiality claim ("every
    delete path records durable intent, atomically with the canonical delete")
    can only be proven at the database boundary, not from a Source.delete()
    unit test.

Core invariant under test: INDEXING MAY FAIL OPEN, but DELETION MAY NOT
DISAPPEAR SILENTLY. If a canonical Source delete commits, a durable local
tombstone must exist — surviving flag-off, worker-absent, and LightRAG-absent.
The tombstone must carry NO document content.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from open_notebook.database.async_migrate import AsyncMigration, AsyncMigrationManager
from open_notebook.integrations.graphrag.models import record_id_for

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "open_notebook" / "database" / "migrations"

UP_SQL = (MIGRATIONS / "24.surrealql").read_text(encoding="utf-8")
DOWN_SQL = (MIGRATIONS / "24_down.surrealql").read_text(encoding="utf-8")

# Fields the tombstone is ALLOWED to have. Anything beyond this is a content /
# metadata leak (the whole point of a tombstone is that it is NOT a copy of the
# deleted document). arm_id is an opaque per-arm fence token (rand::uuid()), not
# content.
ALLOWED_TOMBSTONE_FIELDS = {"id", "source_id", "requested_at", "status", "arm_id"}
FORBIDDEN_TOMBSTONE_SUBSTRINGS = (
    "full_text",
    "content",
    "title",
    "url",
    "file_path",
    "asset",
    "topics",
    "notebook",
    "text",
    "doc_id",  # derivable; must not be duplicated into the row
)


# ===========================================================================
# STRUCTURAL — no database required
# ===========================================================================


class TestMigration24Structure:
    def test_defines_tombstone_table_and_minimal_fields(self):
        assert "DEFINE TABLE IF NOT EXISTS graphrag_deletion SCHEMAFULL" in UP_SQL
        assert (
            "DEFINE FIELD IF NOT EXISTS source_id ON TABLE graphrag_deletion "
            "TYPE record<source>" in UP_SQL
        )
        assert "requested_at ON TABLE graphrag_deletion TYPE datetime" in UP_SQL
        assert "status ON TABLE graphrag_deletion TYPE string" in UP_SQL
        assert "arm_id ON TABLE graphrag_deletion TYPE uuid" in UP_SQL

    def test_event_generates_fresh_arm_token_server_side(self):
        """The per-arm fence token must be minted BY the DB event (server-side,
        no Python/HTTP) on every arm, via SurrealDB's native rand::uuid(). A
        deterministic or timestamp-derived token would not be a safe ABA fence."""
        assert "arm_id = rand::uuid()" in UP_SQL
        # It must NOT be derived from requested_at or the source id.
        assert "arm_id = $before.id" not in UP_SQL
        assert "arm_id = time::now()" not in UP_SQL

    def test_schema_carries_no_content_fields(self):
        """The migration must not define any field that could hold document
        text or provenance — only identity, timestamp, status, and the opaque
        per-arm fence token. Checked against the FLATTENED sql (comments
        stripped), since the file's header comment legitimately names the fields
        it deliberately does NOT store."""
        field_defs = [
            line
            for line in UP_SQL.splitlines()
            if "DEFINE FIELD" in line and "graphrag_deletion" in line
        ]
        # Exactly four fields (source_id, requested_at, status, arm_id); none
        # content-bearing, and no deferred 03-C retry fields yet.
        assert len(field_defs) == 4
        flat = AsyncMigration.from_file(str(MIGRATIONS / "24.surrealql")).sql
        for banned in ("full_text", "title", "url", "file_path", "asset"):
            assert banned not in flat, f"migration 24 must not define {banned}"
        for deferred in ("attempt", "last_error", "next_retry", "resolved_at", "doc_id"):
            assert (
                f"{deferred} ON TABLE graphrag_deletion" not in flat
            ), f"migration 24 must not define 03-C field {deferred}"

    def test_defines_separate_delete_event_not_touching_source_delete(self):
        """A SEPARATE event owns the tombstone; the existing source_delete
        vector-cleanup event (migration 1) is not referenced or altered here."""
        assert (
            "DEFINE EVENT IF NOT EXISTS graphrag_source_delete ON TABLE source" in UP_SQL
        )
        assert "$after == NONE" in UP_SQL
        assert "UPSERT" in UP_SQL  # idempotent write
        # Must not redefine, remove, or reference the existing vector-cleanup event.
        assert "source_delete " not in UP_SQL.replace("graphrag_source_delete ", "")
        assert "source_embedding" not in UP_SQL
        assert "source_insight" not in UP_SQL

    def test_event_upserts_keyed_on_source_identity(self):
        """Idempotency + lossless identity: the tombstone id is derived from the
        source record id via type::thing, and source_id stores the full record
        link — so numeric vs string-numeric ids stay distinct and a repeated
        delete updates one row rather than creating duplicates."""
        assert 'type::thing("graphrag_deletion", $before.id)' in UP_SQL
        assert "source_id = $before.id" in UP_SQL

    def test_down_removes_only_graphrag_objects(self):
        assert "REMOVE EVENT IF EXISTS graphrag_source_delete ON TABLE source" in DOWN_SQL
        assert "REMOVE TABLE IF EXISTS graphrag_deletion" in DOWN_SQL
        # Must NOT drop or touch canonical tables or the existing event.
        for dangerous in (
            "REMOVE TABLE IF EXISTS source;",
            "REMOVE TABLE IF EXISTS source_embedding",
            "REMOVE EVENT IF EXISTS source_delete ",
        ):
            assert dangerous not in DOWN_SQL

    def test_registered_in_manager_up_and_down(self):
        """Migrations are hard-coded in AsyncMigrationManager, not discovered.
        Count is now 24 up + 24 down, and slot 24 is the GraphRAG-03B migration."""
        manager = AsyncMigrationManager()
        assert len(manager.up_migrations) == 24
        assert len(manager.up_migrations) == len(manager.down_migrations)
        assert "graphrag_deletion" in manager.up_migrations[23].sql
        assert "graphrag_source_delete" in manager.up_migrations[23].sql
        assert "REMOVE TABLE IF EXISTS graphrag_deletion" in manager.down_migrations[23].sql

    def test_flattened_migration_is_a_single_valid_statement_stream(self):
        """AsyncMigration.from_file strips comments/blank lines and joins with
        spaces; the result must still be the same multi-statement SQL (this is
        what actually runs on startup)."""
        flat = AsyncMigration.from_file(
            str(MIGRATIONS / "24.surrealql")
        ).sql
        assert "-- " not in flat  # comments stripped
        assert "DEFINE TABLE IF NOT EXISTS graphrag_deletion" in flat
        assert "DEFINE EVENT IF NOT EXISTS graphrag_source_delete" in flat


class TestDeletionHelperIsReadOnlyAndNoHttp:
    def test_helper_imports_no_http_or_lightrag(self):
        src = (
            REPO_ROOT / "open_notebook" / "integrations" / "graphrag" / "deletion.py"
        ).read_text(encoding="utf-8")
        assert "import httpx" not in src
        assert "import lightrag" not in src and "from lightrag" not in src
        # No HTTP client coupling: the module must not import or instantiate the
        # LightRAG client (a prose mention of GraphRAGClient.compute_doc_id as
        # the 03C step is fine; importing/using it here is not).
        assert "graphrag.client import" not in src
        assert "GraphRAGClient(" not in src
        # Read-only, proven by imports rather than prose: the module uses only
        # repo_query (SELECT), never any write helper. This can't be fooled by a
        # docstring that mentions DELETE.
        assert "repo_query" in src
        for writer in (
            "repo_create",
            "repo_update",
            "repo_upsert",
            "repo_delete",
            "repo_insert",
        ):
            assert writer not in src, f"deletion helper must not use {writer}"

    def test_no_draining_command_registered(self):
        """03B creates durable state only. The drain/delete command is 03C and
        must not exist yet."""
        import commands

        registered = set(commands.__all__)
        assert "graphrag_delete_source_command" not in registered
        assert "graphrag_reconcile_command" not in registered

    def test_source_delete_hook_has_no_graphrag_http(self):
        """Source.delete() must NOT gain a LightRAG HTTP call in 03B — deletion
        durability lives in the DB event, not an app-side best-effort call."""
        src = (REPO_ROOT / "open_notebook" / "domain" / "notebook.py").read_text(
            encoding="utf-8"
        )
        assert "graphrag" not in src.lower()
        assert "delete_document_for_source" not in src


# ===========================================================================
# LIVE-DB INTEGRATION — requires a reachable SurrealDB v2
# ===========================================================================


async def _db_reachable() -> bool:
    from open_notebook.database.repository import repo_query

    try:
        await repo_query("RETURN true;")
        return True
    except Exception:
        return False


async def _ensure_migration_applied():
    """Apply migration 24 idempotently (DEFINE ... IF NOT EXISTS)."""
    from open_notebook.database.repository import repo_query

    await repo_query(AsyncMigration.from_file(str(MIGRATIONS / "24.surrealql")).sql)


@pytest_asyncio.fixture
async def live_db():
    """Skip the test if SurrealDB is unreachable; else ensure migration 24 is
    applied and clean up created records afterward. Runs on the test's own event
    loop so no cross-loop connection juggling is needed."""
    from open_notebook.database.repository import ensure_record_id, repo_query

    if not await _db_reachable():
        pytest.skip("SurrealDB not reachable")

    await _ensure_migration_applied()

    # Holds str ids or losslessly-built RecordID objects (ensure_record_id
    # passes RecordIDs through, so escaped ids clean up correctly too).
    created: list = []
    yield created

    for sid in created:
        try:
            await repo_query("DELETE $sid;", {"sid": ensure_record_id(sid)})
        except Exception:
            pass
        try:
            await repo_query(
                "DELETE graphrag_deletion WHERE source_id = $sid;",
                {"sid": ensure_record_id(sid)},
            )
        except Exception:
            pass


async def _tombstones_for(source_id):
    from open_notebook.database.repository import ensure_record_id, repo_query

    return await repo_query(
        "SELECT * FROM graphrag_deletion WHERE source_id = $sid;",
        {"sid": ensure_record_id(source_id)},
    )


@pytest.mark.asyncio
async def test_raw_surrealql_delete_creates_tombstone(live_db):
    """PATH #7 — the bypass hole. A raw ``DELETE source`` runs no Python domain
    code, yet must still leave durable deletion intent, because the event fires
    inside the delete transaction."""
    from open_notebook.database.repository import repo_query

    sid = "source:_t03b_raw"
    live_db.append(sid)
    await repo_query("CREATE source:_t03b_raw SET title='syn', full_text='secret text';")
    await repo_query("DELETE source:_t03b_raw;")

    rows = await _tombstones_for(sid)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert str(rows[0]["source_id"]) == sid


@pytest.mark.asyncio
async def test_source_domain_delete_creates_tombstone(live_db):
    """The Source.delete() domain path also records durable intent."""
    from open_notebook.domain.notebook import Source

    src = Source(title="syn", full_text="secret")
    await src.save()
    sid = str(src.id)
    live_db.append(sid)

    await src.delete()

    rows = await _tombstones_for(sid)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_repository_delete_path_creates_tombstone(live_db):
    """The low-level repo_delete path (used by ObjectModel.delete) also records
    intent — proving coverage does not depend on Source.delete's extra cleanup
    logic."""
    from open_notebook.database.repository import repo_create, repo_delete

    row = await repo_create("source", {"title": "syn", "full_text": "secret"})
    sid = str(row[0]["id"] if isinstance(row, list) else row["id"])
    live_db.append(sid)

    await repo_delete(sid)

    rows = await _tombstones_for(sid)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_tombstone_carries_no_document_content(live_db):
    """Confidentiality: even when the deleted source had full_text/title/asset,
    the tombstone must expose ONLY identity + timestamp + status."""
    from open_notebook.database.repository import repo_query

    sid = "source:_t03b_content"
    live_db.append(sid)
    await repo_query(
        "CREATE source:_t03b_content SET title='Secret Title', "
        "full_text='CONFIDENTIAL BODY', topics=['x'], "
        "asset={file_path:'/private/f.pdf', url:'https://internal/doc?token=abc'};"
    )
    await repo_query("DELETE source:_t03b_content;")

    rows = await _tombstones_for(sid)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) <= ALLOWED_TOMBSTONE_FIELDS, (
        f"tombstone leaked fields: {set(row.keys()) - ALLOWED_TOMBSTONE_FIELDS}"
    )
    blob = str(row).lower()
    for banned in ("confidential", "secret title", "/private/f.pdf", "token=abc", "topics"):
        assert banned not in blob, f"tombstone leaked {banned!r}"


@pytest.mark.asyncio
async def test_repeated_delete_is_idempotent_one_tombstone(live_db):
    """Delete/recreate/redelete of the same canonical id yields ONE effective
    pending tombstone (UPSERT keyed on source identity)."""
    from open_notebook.database.repository import repo_query

    sid = "source:_t03b_idem"
    live_db.append(sid)
    await repo_query("CREATE source:_t03b_idem SET title='a';")
    await repo_query("DELETE source:_t03b_idem;")
    await repo_query("CREATE source:_t03b_idem SET title='b';")
    await repo_query("DELETE source:_t03b_idem;")

    rows = await _tombstones_for(sid)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_rearm_mints_a_fresh_arm_id(live_db):
    """Every arm/re-arm of the same tombstone gets a NEW arm_id, while the row
    stays single (idempotent). This is the per-arm fence 03-C compares on."""
    from open_notebook.database.repository import repo_query

    sid = "source:_t03b_arm"
    live_db.append(sid)
    arms = []
    for _ in range(3):
        await repo_query("CREATE source:_t03b_arm SET title='x';")
        await repo_query("DELETE source:_t03b_arm;")
        rows = await _tombstones_for(sid)
        assert len(rows) == 1  # idempotent: one row across re-arms
        arms.append(str(rows[0]["arm_id"]))
    assert len(set(arms)) == 3, f"arm_id must be fresh each re-arm: {arms}"
    assert all(a for a in arms), "arm_id must be populated"


@pytest.mark.asyncio
async def test_arm_id_is_the_fence_not_requested_at(live_db):
    """The decisive HIGH-4 property: even with requested_at FORCED identical
    across two arms, arm_id differs, and a compare-and-set on the STALE arm_id
    affects ZERO rows while the CURRENT arm_id affects one. This proves arm_id —
    not the timestamp — is the ABA fence, so a stale 03-C drain cannot clear a
    re-armed deletion intent."""
    from open_notebook.database.repository import ensure_record_id, repo_query

    sid = "source:_t03b_fence"
    live_db.append(sid)
    fixed = "d'2020-01-01T00:00:00Z'"

    await repo_query("CREATE source:_t03b_fence SET title='gen A';")
    await repo_query("DELETE source:_t03b_fence;")
    await repo_query(
        f"UPDATE graphrag_deletion SET requested_at={fixed} WHERE source_id=$s;",
        {"s": ensure_record_id(sid)},
    )
    old = (await _tombstones_for(sid))[0]
    old_arm, old_ts = str(old["arm_id"]), str(old["requested_at"])

    # Re-arm (deleted again), then force the SAME requested_at.
    await repo_query("CREATE source:_t03b_fence SET title='gen B';")
    await repo_query("DELETE source:_t03b_fence;")
    await repo_query(
        f"UPDATE graphrag_deletion SET requested_at={fixed} WHERE source_id=$s;",
        {"s": ensure_record_id(sid)},
    )
    new = (await _tombstones_for(sid))[0]
    new_arm, new_ts = str(new["arm_id"]), str(new["requested_at"])

    assert old_ts == new_ts, "precondition: requested_at forced identical"
    assert old_arm != new_arm, "arm_id MUST change on re-arm even when ts collides"

    # CAS on the STALE arm_id → 0 rows (a stale drain cannot resolve the re-armed row).
    stale = await repo_query(
        "UPDATE graphrag_deletion SET status='resolved' "
        "WHERE source_id=$s AND status='pending' AND arm_id=<uuid>$a RETURN BEFORE;",
        {"s": ensure_record_id(sid), "a": old_arm},
    )
    assert len(stale) == 0, "stale arm_id CAS must affect zero rows"

    # CAS on the CURRENT arm_id → 1 row.
    curr = await repo_query(
        "UPDATE graphrag_deletion SET status='resolved' "
        "WHERE source_id=$s AND status='pending' AND arm_id=<uuid>$a RETURN BEFORE;",
        {"s": ensure_record_id(sid), "a": new_arm},
    )
    assert len(curr) == 1, "current arm_id CAS must affect exactly one row"


@pytest.mark.asyncio
async def test_arm_id_is_opaque_and_flag_independent(live_db, monkeypatch):
    """arm_id is an opaque random token (no source content), and it is minted by
    the DB event regardless of the Python feature flag."""
    from open_notebook.database.repository import repo_query

    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "false")
    sid = "source:_t03b_armopaque"
    live_db.append(sid)
    await repo_query(
        "CREATE source:_t03b_armopaque SET title='Secret', full_text='CONFIDENTIAL';"
    )
    await repo_query("DELETE source:_t03b_armopaque;")

    rows = await _tombstones_for(sid)
    assert len(rows) == 1
    arm = str(rows[0]["arm_id"])
    # Populated, UUID-shaped, and carrying none of the source's content/identity.
    assert len(arm) == 36 and arm.count("-") == 4
    assert "confidential" not in arm.lower()
    assert "_t03b_armopaque" not in arm


@pytest.mark.asyncio
async def test_numeric_and_string_numeric_ids_stay_distinct(live_db):
    """source:123 (numeric id) and source:⟨123⟩ (string id '123') are distinct
    records and must produce distinct tombstones — a lossy key would merge two
    documents and orphan one in the sidecar."""
    from open_notebook.database.repository import repo_query

    tables = frozenset({"source"})
    live_db.append("source:123")
    live_db.append(record_id_for("source:⟨123⟩", tables=tables))
    await repo_query("CREATE source:123 SET title='numeric';")
    await repo_query("CREATE type::thing('source', '123') SET title='string-num';")
    await repo_query("DELETE source:123;")
    await repo_query("DELETE type::thing('source', '123');")

    # Look up losslessly: RecordID.parse double-escapes escaped ids, so the
    # string-numeric id must be built via the same lossless helper 03C will use.
    numeric = await _tombstones_for(record_id_for("source:123", tables=tables))
    stringy = await _tombstones_for(record_id_for("source:⟨123⟩", tables=tables))
    assert len(numeric) == 1
    assert len(stringy) == 1
    assert str(numeric[0]["id"]) != str(stringy[0]["id"])


@pytest.mark.asyncio
async def test_escaped_record_id_round_trips(live_db):
    """A canonical escaped id (contains a hyphen → SurrealDB escapes it) must be
    preserved on the tombstone's source_id, so 03C derives the correct doc_id."""
    from open_notebook.database.repository import repo_query

    sid = "source:⟨abc-def⟩"
    live_db.append(sid)
    await repo_query("CREATE type::thing('source', 'abc-def') SET title='escaped';")
    await repo_query("DELETE type::thing('source', 'abc-def');")

    rows = await _tombstones_for(record_id_for(sid, tables=frozenset({"source"})))
    assert len(rows) == 1
    assert str(rows[0]["source_id"]) == sid


@pytest.mark.asyncio
async def test_update_creates_no_tombstone(live_db):
    """The event fires only on delete ($after == NONE); a title/content update
    must not manufacture a spurious deletion intent."""
    from open_notebook.database.repository import repo_query

    sid = "source:_t03b_upd"
    live_db.append(sid)
    await repo_query("CREATE source:_t03b_upd SET title='v1';")
    await repo_query("UPDATE source:_t03b_upd SET title='v2';")

    rows = await _tombstones_for(sid)
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_deleting_reference_edge_unlink_creates_no_tombstone(live_db):
    """Notebook unlink semantics: removing a notebook→source membership edge
    does NOT delete the source, so it must NOT create a deletion tombstone
    (eligibility ≠ membership; forensic §6)."""
    from open_notebook.database.repository import repo_query

    sid = "source:_t03b_unlink"
    live_db.append(sid)
    await repo_query("CREATE source:_t03b_unlink SET title='kept';")
    await repo_query("CREATE notebook:_t03b_nb SET name='nb';")
    await repo_query("RELATE source:_t03b_unlink->reference->notebook:_t03b_nb;")
    # Unlink only (what Notebook.delete() default does): delete the edge.
    await repo_query("DELETE reference WHERE out = notebook:_t03b_nb;")

    rows = await _tombstones_for(sid)
    assert len(rows) == 0
    # cleanup extras
    await repo_query("DELETE notebook:_t03b_nb;")


@pytest.mark.asyncio
async def test_deleting_non_source_record_creates_no_tombstone(live_db):
    """The event is scoped ON TABLE source. Deleting a note (or any non-source
    record) must not be able to create a graphrag_deletion row — the source_id
    field is TYPE record<source>, so a non-source identity could never form a
    valid tombstone anyway."""
    from open_notebook.database.repository import repo_query

    await repo_query("CREATE note:_t03b_note SET title='n', content='c';")
    await repo_query("DELETE note:_t03b_note;")

    rows = await repo_query(
        "SELECT * FROM graphrag_deletion WHERE source_id = note:_t03b_note;"
    )
    # Not even structurally storable (record<source>), and event never fires.
    assert rows == []


@pytest.mark.asyncio
async def test_existing_vector_cleanup_still_fires_alongside_tombstone(live_db):
    """No regression: the existing source_delete event must still purge
    source_embedding, AND the new event must create a tombstone — the two
    events coexist on the source table."""
    from open_notebook.database.repository import repo_query

    sid = "source:_t03b_vec"
    live_db.append(sid)
    await repo_query("CREATE source:_t03b_vec SET title='syn', full_text='body';")
    await repo_query(
        "CREATE source_embedding SET source = source:_t03b_vec, "
        "order = 0, content = 'chunk', embedding = [0.1, 0.2];"
    )
    await repo_query("DELETE source:_t03b_vec;")

    embeddings = await repo_query(
        "SELECT * FROM source_embedding WHERE source = source:_t03b_vec;"
    )
    tombstones = await _tombstones_for(sid)
    assert embeddings == [], "existing vector cleanup regressed"
    assert len(tombstones) == 1, "new tombstone missing"


@pytest.mark.asyncio
async def test_tombstone_created_regardless_of_feature_flag(live_db, monkeypatch):
    """DB lifecycle correctness is independent of the runtime enable flag. With
    OPEN_NOTEBOOK_GRAPHRAG_ENABLED explicitly false, a source delete must STILL
    write the tombstone — the event cannot read the Python flag, and deletion
    intent must never be gated by it (failure-matrix row 18)."""
    from open_notebook.database.repository import repo_query

    monkeypatch.setenv("OPEN_NOTEBOOK_GRAPHRAG_ENABLED", "false")
    sid = "source:_t03b_flagoff"
    live_db.append(sid)
    await repo_query("CREATE source:_t03b_flagoff SET title='syn';")
    await repo_query("DELETE source:_t03b_flagoff;")

    rows = await _tombstones_for(sid)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_down_migration_removes_only_graphrag_objects(live_db):
    """Rollback: applying 24_down removes the tombstone table and the GraphRAG
    event, while the canonical source table and the existing source_delete
    vector-cleanup event survive untouched. Restores state afterward so the rest
    of the suite still sees migration 24."""
    from open_notebook.database.repository import repo_query

    def _first(res):
        if isinstance(res, dict):
            return res
        if isinstance(res, list) and res:
            return res[0]
        return {}

    try:
        # Apply DOWN.
        await repo_query(AsyncMigration.from_file(str(MIGRATIONS / "24_down.surrealql")).sql)

        db_info = _first(await repo_query("INFO FOR DB;"))
        assert "graphrag_deletion" not in db_info.get("tables", {})
        assert "source" in db_info.get("tables", {}), "canonical table must survive"

        src_info = _first(await repo_query("INFO FOR TABLE source;"))
        events = src_info.get("events", {})
        assert "graphrag_source_delete" not in events, "graphrag event must be removed"
        assert "source_delete" in events, "existing vector-cleanup event must survive"
    finally:
        # Restore migration 24 for subsequent tests (idempotent).
        await repo_query(AsyncMigration.from_file(str(MIGRATIONS / "24.surrealql")).sql)


@pytest.mark.asyncio
async def test_list_pending_deletions_enumerates_tombstone(live_db):
    """The 03C enumeration primitive returns pending tombstones with the
    canonical source_id preserved (so 03C can derive doc_id)."""
    from open_notebook.database.repository import repo_query
    from open_notebook.integrations.graphrag.deletion import list_pending_deletions

    sid = "source:_t03b_list"
    live_db.append(sid)
    await repo_query("CREATE source:_t03b_list SET title='syn';")
    await repo_query("DELETE source:_t03b_list;")

    pending = await list_pending_deletions()
    matching = [t for t in pending if t.source_id == sid]
    assert len(matching) == 1
    assert matching[0].status == "pending"
    assert matching[0].requested_at is not None
    # arm_id must be surfaced for 03-C's compare-and-set resolution.
    assert matching[0].arm_id and len(matching[0].arm_id) == 36
