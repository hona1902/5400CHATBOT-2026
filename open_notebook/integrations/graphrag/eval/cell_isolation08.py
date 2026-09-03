"""GraphRAG-08E.1 diagnostic experimental-cell isolation (EVALUATION-ONLY).

Nothing in production imports this. It hardens the GraphRAG-08E concurrency
diagnostic so that each experimental cell — one (concurrency level, repetition)
pair — begins from LightRAG extraction/storage state that is provably independent
of every other cell. This closes the cell-contamination defect found at the
08E live preflight: the same synthetic Source content recurs across levels and
repetitions, and LightRAG's LLM response cache (``kv_store_llm_response_cache.json``)
would let a later cell reuse a prior cell's extraction, invalidating the
failure-rate-vs-concurrency comparison.

Pinned LightRAG v1.5.6 (commit b33c6b0) storage forensic — verified from source:
  * A ``workspace`` scopes EVERY on-disk store by SUBDIRECTORY:
    ``working_dir/[workspace/]kv_store_<namespace>.json`` (kg/json_kv_impl.py:141-151,
    which is the backend for the LLM response cache), and
    ``working_dir/[workspace/]graph_<namespace>.graphml`` (kg/networkx_impl.py:33),
    and the FAISS/vector stores likewise (kg/faiss_impl.py:217-219).
  * The in-memory shared storage is keyed by ``get_final_namespace(namespace,
    workspace)`` (kg/shared_storage.py:208) — distinct workspaces do not share
    in-process data.
  * BUT the HTTP server's workspace is FIXED at startup (``--workspace`` = "Default
    workspace for all storage", api/config.py:474-477); the insert endpoints accept
    no per-request workspace. So per-cell isolation on ONE running server is NOT
    possible — a fresh server process (unique WORKSPACE) is required per cell.

Selected isolation strategy (OPTION B + per-cell workspace): each cell runs against
a FRESH LightRAG sidecar process configured with a UNIQUE per-cell ``WORKSPACE``.
Fresh process ⇒ no in-memory cache carryover; unique workspace subdirectory ⇒ no
on-disk carryover of the LLM cache, graph, KV, vector, or doc-status state. Cell
cleanup disposes ONLY that cell's own workspace subdirectory — never the working_dir
root, never another workspace, never foreign/shared storage.

This module is OFFLINE: it defines the cell-isolation CONTRACT (identity, uniqueness
guard, validity gate, ownership-fail-closed cleanup) and drives it through an INJECTED
provisioner. The live provisioner (which restarts the sidecar per cell) is a future
live-phase component; nothing here starts a sidecar, calls a provider, or mutates a DB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Set

# LightRAG v1.5.6 workspace sanitisation (api/config.py:912) — mirror it so a cell
# workspace is always a valid LightRAG workspace identifier.
_WS_SANITISE = re.compile(r"[^a-zA-Z0-9_]")

#: Prefix for a diagnostic cell's LightRAG workspace (content-free).
CELL_WORKSPACE_PREFIX = "gr08e_"

#: The LightRAG stores whose paths a workspace must isolate (verified filenames from
#: the pinned v1.5.6 rag_storage layout). Used to PROVE per-cell path isolation.
_LIGHTRAG_STORE_FILES = (
    "kv_store_llm_response_cache.json",  # the extraction cache — the contamination risk
    "kv_store_doc_status.json",
    "kv_store_full_docs.json",
    "kv_store_text_chunks.json",
    "kv_store_full_entities.json",
    "kv_store_full_relations.json",
    "kv_store_entity_chunks.json",
    "kv_store_relation_chunks.json",
    "graph_chunk_entity_relation.graphml",
    "vdb_chunks.json",
    "vdb_entities.json",
    "vdb_relationships.json",
)


def sanitize_workspace(name: str) -> str:
    """Sanitise to LightRAG's allowed workspace charset (alnum + underscore)."""
    return _WS_SANITISE.sub("_", name)


# ---- errors (all fail-closed) ----------------------------------------------


class CellIsolationError(RuntimeError):
    """A cell-isolation invariant was violated (fail closed)."""


class CellOwnershipError(CellIsolationError):
    """A cleanup/ownership boundary could not be proven (fail closed)."""


class CellValidityError(CellIsolationError):
    """A cell could not be proven isolated/fresh/owned before indexing."""


class DiagnosticCellIsolationFailure(CellIsolationError):
    """The cell isolation contract failed during a run — the sweep must STOP (no
    mid-run repair, task §19)."""


# ---- cell identity ---------------------------------------------------------


@dataclass(frozen=True)
class CellIdentity:
    """Content-free identity of one experimental cell = (level, repetition)."""

    run_id: str
    concurrency: int
    repetition: int

    def __post_init__(self) -> None:
        if not re.match(r"^[A-Za-z0-9]{4,32}$", self.run_id):
            raise CellIsolationError(f"invalid run_id {self.run_id!r}")
        if self.concurrency < 1 or self.repetition < 1:
            raise CellIsolationError("concurrency and repetition must be >= 1")

    @property
    def cell_id(self) -> str:
        return f"{self.run_id}_c{self.concurrency}_r{self.repetition}"

    @property
    def workspace(self) -> str:
        """Unique, LightRAG-valid per-cell workspace (isolates every store)."""
        return sanitize_workspace(f"{CELL_WORKSPACE_PREFIX}{self.cell_id}")

    def as_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "concurrency": self.concurrency,
            "repetition": self.repetition,
            "cell_id": self.cell_id,
            "workspace": self.workspace,
        }


def cell_storage_dir(working_dir: str, workspace: str) -> str:
    """LightRAG's per-workspace storage dir: ``working_dir/[workspace/]`` (v1.5.6
    json_kv_impl.py:142-147). An empty workspace maps to working_dir (the shared,
    NON-isolated default — never used for a cell)."""
    import os

    if workspace:
        return os.path.join(working_dir, workspace)
    return working_dir


def cell_storage_paths(working_dir: str, workspace: str) -> Dict[str, str]:
    """Every LightRAG store path for a workspace — used to PROVE path isolation."""
    import os

    d = cell_storage_dir(working_dir, workspace)
    return {name: os.path.join(d, name) for name in _LIGHTRAG_STORE_FILES}


def cross_cell_storage_isolated(
    working_dir: str, ws_a: str, ws_b: str
) -> bool:
    """True iff two distinct cell workspaces share NO storage path (esp. the LLM
    cache). Proves ``CROSS_CELL_LLM_CACHE_REUSE_POSSIBLE = NO`` at the storage layer
    (task §8/§11/§28)."""
    if not ws_a or not ws_b or ws_a == ws_b:
        return False
    pa = set(cell_storage_paths(working_dir, ws_a).values())
    pb = set(cell_storage_paths(working_dir, ws_b).values())
    return pa.isdisjoint(pb)


# ---- uniqueness registry (defense in depth, task §22) ----------------------


class CellRegistry:
    """Tracks cell workspace identities used in one sweep; a duplicate fails closed
    BEFORE any live work (task §22/§30)."""

    def __init__(self) -> None:
        self._workspaces: Set[str] = set()
        self._cell_ids: Set[str] = set()

    def register(self, identity: CellIdentity) -> None:
        ws = identity.workspace
        cid = identity.cell_id
        if ws in self._workspaces or cid in self._cell_ids:
            raise CellOwnershipError(
                f"duplicate cell isolation identity ({cid} / {ws}) — refusing reuse"
            )
        self._workspaces.add(ws)
        self._cell_ids.add(cid)

    @property
    def count(self) -> int:
        return len(self._workspaces)


# ---- provisioner contract (injected; live impl restarts the sidecar) -------


@dataclass(frozen=True)
class CellProvision:
    """Result of provisioning a cell's fresh LightRAG state (content-free)."""

    workspace: str
    storage_dir: str
    #: Proof that this cell cannot observe a prior cell's extraction cache — the
    #: live provisioner sets this only after a FRESH process + empty workspace dir.
    fresh_extraction_state: bool
    owned: bool


@dataclass(frozen=True)
class CellDisposal:
    disposed: bool
    owned: bool


class CellProvisioner(Protocol):
    """Provision/dispose fresh, cell-owned LightRAG state. The LIVE implementation
    restarts the sidecar with ``WORKSPACE=identity.workspace`` and a run-owned storage
    root, then disposes ONLY that workspace subdirectory. OFFLINE tests inject a mock.
    Neither the protocol nor this module starts a sidecar or calls a provider.

    Hard requirements the LIVE provisioner MUST satisfy (the offline contract trusts,
    but cannot execute, these — carried from independent review):
      * ``provision`` MUST be ATOMIC/self-cleaning: if it raises after creating partial
        resources, it must dispose them itself, because a raise inside ``__aenter__``
        means ``__aexit__`` never runs and this layer holds no ``provision`` to dispose
        (review LOW-2).
      * ``fresh_extraction_state=True`` MUST be set only after VERIFYING the cell's
        workspace directory is actually empty (a fresh process alone does not prove an
        on-disk workspace left by a crashed prior run with the same ``run_id`` is empty);
        do not merely assert it (review LOW-3)."""

    async def provision(self, identity: CellIdentity) -> CellProvision: ...

    async def dispose(
        self, identity: CellIdentity, provision: CellProvision
    ) -> CellDisposal: ...


@dataclass(frozen=True)
class CellValidity:
    isolation_valid: bool
    cache_fresh: bool
    ownership_valid: bool
    reason: str

    @property
    def valid(self) -> bool:
        return self.isolation_valid and self.cache_fresh and self.ownership_valid

    def as_dict(self) -> Dict[str, object]:
        return {
            "isolation_valid": self.isolation_valid,
            "cache_fresh": self.cache_fresh,
            "ownership_valid": self.ownership_valid,
            "valid": self.valid,
            "reason": self.reason,
        }


@dataclass
class DiagnosticCell:
    identity: CellIdentity
    provision: CellProvision
    validity: CellValidity


class _CellContext:
    """Async context manager for one experimental cell (task §10)."""

    def __init__(
        self,
        identity: CellIdentity,
        *,
        provisioner: CellProvisioner,
        registry: CellRegistry,
        working_dir: str,
    ) -> None:
        self._identity = identity
        self._provisioner = provisioner
        self._registry = registry
        self._working_dir = working_dir
        self._provision: Optional[CellProvision] = None

    async def __aenter__(self) -> DiagnosticCell:
        # 1) unique identity (fail closed on reuse) BEFORE any provisioning.
        self._registry.register(self._identity)
        # 2) provision fresh, cell-owned state.
        prov = await self._provisioner.provision(self._identity)
        self._provision = prov
        # 3) validity gate (task §17/§18): fresh cache + owned + storage matches the
        #    cell's own isolated workspace dir. Any failure fails closed (no run).
        expected_dir = cell_storage_dir(self._working_dir, self._identity.workspace)
        isolation_ok = (
            bool(prov.workspace)
            and prov.workspace == self._identity.workspace
            and prov.storage_dir == expected_dir
        )
        validity = CellValidity(
            isolation_valid=isolation_ok,
            cache_fresh=bool(prov.fresh_extraction_state),
            ownership_valid=bool(prov.owned),
            reason=(
                "CELL_ISOLATION_VALID"
                if (isolation_ok and prov.fresh_extraction_state and prov.owned)
                else "CELL_ISOLATION_INVALID"
            ),
        )
        if not validity.valid:
            # No mid-run repair (task §19): dispose what we made, then STOP.
            try:
                await self._provisioner.dispose(self._identity, prov)
            except Exception:  # noqa: BLE001 - never mask the isolation failure
                pass
            raise DiagnosticCellIsolationFailure(
                f"cell {self._identity.cell_id} isolation invalid ({validity.reason})"
            )
        return DiagnosticCell(
            identity=self._identity, provision=prov, validity=validity
        )

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        # Dispose ONLY this cell's owned resources; fail closed on unproven ownership.
        # A cleanup failure that supersedes a body exception CHAINS it (``from exc``,
        # review LOW-1) so the original cause is never lost.
        if self._provision is not None:
            disposal = await self._provisioner.dispose(self._identity, self._provision)
            if not disposal.owned:
                raise CellOwnershipError(
                    f"cell {self._identity.cell_id} cleanup ownership not proven"
                ) from exc
            if not disposal.disposed:
                raise CellIsolationError(
                    f"cell {self._identity.cell_id} cleanup did not complete"
                ) from exc
        return False  # never suppress an exception from the cell body


def diagnostic_cell08(
    identity: CellIdentity,
    *,
    provisioner: CellProvisioner,
    registry: CellRegistry,
    working_dir: str,
) -> _CellContext:
    """Enter one isolated experimental cell (task §10). See module docstring."""
    return _CellContext(
        identity, provisioner=provisioner, registry=registry, working_dir=working_dir
    )


__all__: List[str] = [
    "CELL_WORKSPACE_PREFIX",
    "sanitize_workspace",
    "CellIsolationError",
    "CellOwnershipError",
    "CellValidityError",
    "DiagnosticCellIsolationFailure",
    "CellIdentity",
    "cell_storage_dir",
    "cell_storage_paths",
    "cross_cell_storage_isolated",
    "CellRegistry",
    "CellProvision",
    "CellDisposal",
    "CellProvisioner",
    "CellValidity",
    "DiagnosticCell",
    "diagnostic_cell08",
]
