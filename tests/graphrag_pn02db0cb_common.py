"""Shared builders for the PN02D-B0C-B real-provider-wiring tests (not a test module).

EVALUATION-ONLY test support. Provides mocks/fakes for every injected boundary so the
REAL adapter classes are exercised with ZERO provider traffic and ZERO normal-DB
access: a fake process controller + health prober (no Docker), mock LightRAG httpx
transports (index/track/GD/delete), a fake embedding provider + fake vector DB, and a
test-only live-capability factory (design §14 — mock operator grant + synthetic run
identity; NOT a real provider run).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Set, Tuple
from unittest import mock

import httpx

from open_notebook.integrations.graphrag.eval import (
    authmintlivepn02d as _authmint,
)
from open_notebook.integrations.graphrag.eval.attestpn02d import (
    mint_real_preflight_authorization,
)
from open_notebook.integrations.graphrag.eval.authmintlivepn02d import (
    GitBaselineAttestation,
    LiveProviderRunAuthorization,
    OperatorRunGrant,
    RealTrustedB1R2Reader,
    frozen_b1_operator_grant_template,
    mint_live_provider_run_authorization,
)
from open_notebook.integrations.graphrag.eval.cell_provisioner08 import (
    CellProcessHandle,
    CellProcessSpec,
)
from open_notebook.integrations.graphrag.eval.datasetpn02 import (
    FixturePN02,
    load_fixture,
    verify_fixture_hash,
)
from open_notebook.integrations.graphrag.eval.docidpn02d import (
    compute_derived_document_id,
)
from open_notebook.integrations.graphrag.eval.driver_live_pn02d import LiveB1Seams
from open_notebook.integrations.graphrag.eval.runtimelivepn02d import HealthObservation
from open_notebook.integrations.graphrag.eval.vectoradapterpn02d import (
    ActiveEmbeddingModelAttestation,
)

EMBED_DIM = 1536
TEST_COMMIT = "0c0cb0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0"
TEST_TAG = "graphrag-pn02db0cb-test-baseline"
TEST_RUN_ID = "pn02db0cb-test-run"
#: A SYNTHETIC (test-only) approved B1-R2 checkpoint tag — proves the B1-R2 validation
#: mechanism CAN pass later. It is NOT a real approved identity (PN02D-B1-R2 is
#: NOT_STARTED); the real-git B1-R2 reader observes no such tag and fails closed.
TEST_B1R2_TAG = "graphrag-pn02db1r2-test-approved"
PORT_BASE = 23000


# --------------------------------------------------------------------------- #
# Fixture helpers + synthetic embeddings
# --------------------------------------------------------------------------- #

def fixture() -> FixturePN02:
    return load_fixture()


def source_index(fx: FixturePN02) -> Dict[str, int]:
    return {k: i for i, k in enumerate(sorted(fx.source_keys))}


def _basis_vector(index: int) -> List[float]:
    """A 1536-d basis vector: 1.0 at ``index``, else 0.0 (a unique per-source embedding)."""
    vec = [0.0] * EMBED_DIM
    vec[index % EMBED_DIM] = 1.0
    return vec


def question_relevance(fx: FixturePN02) -> Dict[str, Set[str]]:
    """question text -> the set of relevant (required∪optional) fixture Source keys."""
    rel: Dict[str, Set[str]] = defaultdict(set)
    for q in fx.queries:
        rel[q.question].update(q.required_source_ids)
        rel[q.question].update(q.optional_support_source_ids)
    return rel


def record_id_for_key(key: str) -> str:
    """A synthetic isolated ``source`` record id for a fixture key (test-owned)."""
    return f"source:pn02_{key}"


# --------------------------------------------------------------------------- #
# Fake runtime seams (no Docker)
# --------------------------------------------------------------------------- #

@dataclass
class FakeProcessController:
    """Records starts/terminations; issues unique container ids; boots NO container."""

    started: List[CellProcessSpec] = field(default_factory=list)
    terminated: List[str] = field(default_factory=list)
    _counter: int = 0

    async def start(self, spec: CellProcessSpec) -> CellProcessHandle:
        self._counter += 1
        self.started.append(spec)
        return CellProcessHandle(
            identifier=f"fakecell-{self._counter}-{spec.cell_id}", kind="fake"
        )

    async def terminate(self, handle: CellProcessHandle, *, graceful_timeout_s: float):
        self.terminated.append(handle.identifier)
        from open_notebook.integrations.graphrag.eval.cell_provisioner08 import (
            TerminationResult,
        )

        return TerminationResult(stopped=True, forced=False)


@dataclass
class FakeHealthProber:
    healthy: bool = True
    core_version: str = "1.5.6"

    async def probe(self, *, base_url: str, host: str, port: int) -> HealthObservation:
        return HealthObservation(
            reachable=self.healthy,
            healthy=self.healthy,
            core_version=self.core_version,
            workspace="",
        )


@dataclass
class FakePreflightRunner:
    """Provider-free preflight runner fake (B0CB-M1): records launched argvs; launches
    NO container. ``version_signals`` returns the three pinned provider-free signals so
    ``attest_version`` PASSes; set ``version_attested=False`` to simulate a failed
    preflight (M2)."""

    version_attested: bool = True
    #: PN02D-B1-PF1: when False, ``wait_ready`` fails closed (models a never-ready sidecar).
    ready: bool = True
    launched: List[List[str]] = field(default_factory=list)
    terminated: List[str] = field(default_factory=list)
    #: PN02D-B1-PF1: ordered record of readiness/version calls per handle (ordering test).
    calls: List[str] = field(default_factory=list)
    _counter: int = 0

    async def launch(self, argv):
        self._counter += 1
        self.launched.append(list(argv))
        # container name is the argv value after --name
        argv_l = list(argv)
        name = argv_l[argv_l.index("--name") + 1] if "--name" in argv_l else f"pf-{self._counter}"
        return CellProcessHandle(identifier=f"preflight-{self._counter}-{name}", kind="fake")

    async def wait_ready(self, handle: CellProcessHandle) -> None:
        # PN02D-B1-PF1: readiness gate. Must be awaited BEFORE version_signals.
        self.calls.append(f"wait_ready:{handle.identifier}")
        if not self.ready:
            # Import here to avoid a module import cycle at collection time.
            from open_notebook.integrations.graphrag.eval.runtimelivepn02d import (
                PreflightRunError,
            )

            raise PreflightRunError("fake sidecar never became ready (PF1 test)")

    async def version_signals(self, handle: CellProcessHandle) -> Tuple[str, str, str]:
        self.calls.append(f"version_signals:{handle.identifier}")
        if self.version_attested:
            return "1.5.6", "1.5.6", "v1.5.6"
        return "0.0.0", "0.0.0", "0.0.0"  # fails attest_version → preflight not passed

    async def terminate(self, handle: CellProcessHandle) -> None:
        self.terminated.append(handle.identifier)


@dataclass
class FakeDockerCLI:
    """A scripted Docker transport for driving the REAL RealProviderFreePreflightRunner
    logic without a real Docker (B0CB-M1M2-R1 tests). Each field scripts one boundary.
    Records stop/rm/network_rm for cleanup assertions."""

    net_ok: bool = True
    run_returncode: int = 0
    run_raises: bool = False
    container_running: Optional[bool] = True
    health_code: Optional[int] = 200
    health_core: str = "1.5.6"
    installed_version: str = "1.5.6"
    image_label: str = "v1.5.6"
    stopped: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    nets_removed: List[str] = field(default_factory=list)
    nets_created: List[str] = field(default_factory=list)

    def network_create_internal(self, name: str) -> bool:
        if self.net_ok:
            self.nets_created.append(name)
        return self.net_ok

    def run(self, argv, timeout: float = 90.0):
        if self.run_raises:
            raise RuntimeError("docker run boom")
        return SimpleNamespace(returncode=self.run_returncode, stdout="", stderr="")

    def inspect_state(self, name: str):
        # PN02D-B1-PF1: return a real SidecarObservation so wait_healthy's with_health()
        # accepts it (the pre-PF1 SimpleNamespace lacked the coarse-observation fields).
        from open_notebook.integrations.graphrag.eval.sidecar_diag08 import (
            SidecarObservation,
        )

        running = self.container_running
        return SidecarObservation(
            container_created=None if running is None else True,
            container_running=running,
            container_health_state=None,
            container_exit_code=None,
            container_restart_count=None,
            port_open=None,
            health_http_reachable=None,
            health_http_status_class=None,
            healthy=False,
            timeout_reached=False,
        )

    def health(self, name: str):
        return (self.health_code, self.health_core, "healthy")

    def runtime_version(self, name: str) -> str:
        return self.installed_version

    def image_version_label(self, image: str = "") -> str:
        return self.image_label

    def stop(self, name: str, timeout: float = 20.0) -> bool:
        self.stopped.append(name)
        return True

    def rm(self, name: str, force: bool = True) -> bool:
        self.removed.append(name)
        return True

    def network_rm(self, name: str) -> bool:
        self.nets_removed.append(name)
        return True


def fake_git_runner(
    *,
    head: str,
    branch: str,
    exact_tag: str,
    tag_peel: str,
    porcelain: str = "",
):
    """A scripted git command boundary for read_git_baseline (B0CB-H2-R1 tests).

    Drives the REAL parser: `describe --tags --exact-match` returns ``exact_tag`` (may
    be empty for an untagged HEAD), and no branch-name fallback is applied.
    """

    def _run(args):
        a = list(args)
        if a[:2] == ["rev-parse", "HEAD"]:
            return head
        if a[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return branch
        if a[:1] == ["describe"]:
            return exact_tag
        if a[:2] == ["rev-list", "-n"]:
            return tag_peel
        if a[:2] == ["status", "--porcelain"]:
            return porcelain
        return ""

    return _run


def clean_git_baseline(commit: str = TEST_COMMIT, tag: str = TEST_TAG) -> GitBaselineAttestation:
    """A CLEAN git-baseline attestation matching the approved commit/tag (B0CB-H2)."""
    return GitBaselineAttestation(
        branch="feature/graphrag-lifecycle",
        head_commit=commit,
        head_tag=tag,
        tag_peel_commit=commit,
        staged_count=0,
        unstaged_count=0,
        untracked_execution_affecting_count=0,
    )


def dirty_git_baseline(commit: str = TEST_COMMIT, tag: str = TEST_TAG) -> GitBaselineAttestation:
    """A DIRTY git-baseline attestation (unstaged changes present) — must be refused."""
    return GitBaselineAttestation(
        branch="feature/graphrag-lifecycle",
        head_commit=commit,
        head_tag=tag,
        tag_peel_commit=commit,
        staged_count=0,
        unstaged_count=3,
        untracked_execution_affecting_count=0,
    )


class DeterministicPortAllocator:
    def __init__(self, base: int = PORT_BASE) -> None:
        self._next = base

    def __call__(self) -> int:
        port = self._next
        self._next += 1
        return port


async def fake_version_signal_reader(_notebook_id: str) -> Tuple[str, str]:
    return "1.5.6", "v1.5.6"


def fake_storage_dir_allocator(cell_id: str) -> str:
    return f"eval-store://{cell_id}"


# --------------------------------------------------------------------------- #
# Fake corpus seams + fake vector DB
# --------------------------------------------------------------------------- #

@dataclass
class FakeCorpusDB:
    """A fake isolated corpus: source_embedding rows keyed by record id (no real DB)."""

    embeddings: Dict[str, List[float]] = field(default_factory=dict)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    created: List[str] = field(default_factory=list)
    embed_calls: int = 0
    torn_down: bool = False
    #: foreign (non-member) rows a global top-K would surface — must NEVER be fetched.
    foreign_embeddings: Dict[str, List[float]] = field(default_factory=dict)

    def source_creator(self):
        async def _create(key: str, _title: str, _text: str) -> str:
            rid = record_id_for_key(key)
            self.created.append(rid)
            return rid

        return _create

    def reference_linker(self):
        async def _link(source_record_id: str, notebook_record_id: str) -> None:
            self.edges.append((source_record_id, notebook_record_id))

        return _link

    def source_embedder(self, fx: FixturePN02):
        idx = source_index(fx)
        key_by_record = {record_id_for_key(k): k for k in fx.source_keys}

        async def _embed(source_record_id: str) -> int:
            self.embed_calls += 1
            key = key_by_record[source_record_id]
            self.embeddings[source_record_id] = _basis_vector(idx[key])
            return 1

        return _embed

    def member_row_fetcher(self):
        async def _fetch(
            source_record_ids: Sequence[str], _query_embedding: Sequence[float]
        ) -> Sequence[Tuple[str, Sequence[float]]]:
            # Honours WHERE source IN $ids: ONLY the requested member record ids are
            # returned — foreign rows (even higher-similarity) are never fetched.
            out: List[Tuple[str, Sequence[float]]] = []
            for rid in source_record_ids:
                emb = self.embeddings.get(rid)
                if emb is not None:
                    out.append((rid, list(emb)))
            return out

        return _fetch

    async def teardown(self) -> None:
        self.torn_down = True


def make_query_embed_fn(fx: FixturePN02):
    """A fake embedder: query vec = normalized sum of the question's relevant basis vecs."""
    idx = source_index(fx)
    rel = question_relevance(fx)

    async def _embed(question: str) -> List[float]:
        keys = rel.get(question, set())
        vec = [0.0] * EMBED_DIM
        for k in keys:
            vec[idx[k] % EMBED_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            # a nonzero, dimension-correct default (still 1536-d)
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    return _embed


def make_model_attestor(
    *, provider: str = "openrouter", model: str = "openai/text-embedding-3-small",
    dimension: int = EMBED_DIM,
):
    async def _attest() -> ActiveEmbeddingModelAttestation:
        return ActiveEmbeddingModelAttestation(
            provider=provider, model=model, dimension=dimension
        )

    return _attest


# --------------------------------------------------------------------------- #
# Mock LightRAG httpx transport (index / track / GD / delete), notebook-scoped
# --------------------------------------------------------------------------- #

def build_mock_transport(
    fx: FixturePN02,
    base_url_to_notebook: Dict[str, str],
    *,
    index_status: str = "processed",
    failing_source_keys: Optional[Set[str]] = None,
    calls: Optional[Dict[str, int]] = None,
) -> httpx.MockTransport:
    """A stateful mock transport for all LightRAG wire seams. NO real network.

    GD results are scoped to the notebook that owns the request's base_url; a successful
    delete removes the source from that base_url's GD results (modelling the derived
    store), so NB_A losing SH_AB never affects NB_B.
    """
    rel = question_relevance(fx)
    doc_to_key = {compute_derived_document_id(k): k for k in fx.source_keys}
    deleted: Dict[str, Set[str]] = defaultdict(set)
    failing = set(failing_source_keys or ())
    counts = calls if calls is not None else {}

    def _bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    def handler(request: httpx.Request) -> httpx.Response:
        base_url = f"{request.url.scheme}://{request.url.host}:{request.url.port}"
        nb = base_url_to_notebook.get(base_url)
        path = request.url.path
        method = request.method

        if method == "POST" and path == "/documents/text":
            _bump("index_submit")
            body = json.loads(request.content or b"{}")
            fs = body.get("file_source", "x")
            return httpx.Response(
                200, json={"status": "success", "track_id": f"trk-{fs}", "message": "ok"}
            )
        if method == "GET" and path.startswith("/documents/track_status/"):
            _bump("track_status")
            track_id = path.rsplit("/", 1)[-1]
            src = track_id[len("trk-") :] if track_id.startswith("trk-") else ""
            status = "failed" if src in failing else index_status
            return httpx.Response(
                200,
                json={
                    "documents": [{"status": status}],
                    "status_summary": {status: 1},
                    "total_count": 1,
                },
            )
        if method == "POST" and path == "/query/data":
            _bump("query_data")
            body = json.loads(request.content or b"{}")
            question = body.get("query", "")
            members = set(fx.members_of(nb)) if nb else set()
            relevant = rel.get(question, set()) & members
            relevant -= deleted.get(base_url, set())
            chunks = [{"file_path": s} for s in sorted(relevant)]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "chunks": chunks,
                        "references": [],
                        "entities": [],
                        "relationships": [],
                    }
                },
            )
        if method == "DELETE" and path == "/documents/delete_document":
            _bump("delete")
            body = json.loads(request.content or b"{}")
            for doc_id in body.get("doc_ids", []):
                key = doc_to_key.get(doc_id)
                if key is not None:
                    deleted[base_url].add(key)
            return httpx.Response(
                200, json={"status": "deletion_started", "message": "ok"}
            )
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- #
# Test-only live-capability factory (design §14 — NOT a real provider run)
# --------------------------------------------------------------------------- #

def frozen_test_grant(
    *,
    run_id: str = TEST_RUN_ID,
    commit: str = TEST_COMMIT,
    tag: str = TEST_TAG,
    b1_r2_checkpoint: str = TEST_B1R2_TAG,
) -> OperatorRunGrant:
    return frozen_b1_operator_grant_template(
        run_id=run_id,
        implementation_checkpoint_commit=commit,
        implementation_checkpoint_tag=tag,
        b1_r2_checkpoint=b1_r2_checkpoint,
        approved_git_commit=commit,
        approved_git_tag=tag,
    )


def b1r2_git_runner(
    *, tag: str = TEST_B1R2_TAG, exists: bool = True,
    peel: str = TEST_COMMIT, head: str = TEST_COMMIT,
):
    """A scripted git boundary for the REAL RealTrustedB1R2Reader (B0CB-RR3-H1 tests).

    Drives the REAL reader logic (not a fake reader): ``git tag --list <name>`` returns
    the name iff ``exists``; ``git rev-list -n 1 refs/tags/<name>`` returns ``peel``;
    ``git rev-parse HEAD`` returns ``head``. The reader mints the unforgeable
    ``TrustedB1R2Observation`` itself.
    """

    def _run(args):
        a = list(args)
        if a[:2] == ["rev-parse", "HEAD"]:
            return head
        if a[:2] == ["tag", "--list"]:
            name = a[2] if len(a) > 2 else ""
            return name if (exists and name) else ""
        if a[:2] == ["rev-list", "-n"]:
            return peel if exists else ""
        return ""

    return _run


def b1r2_reader_ok(
    *, tag: str = TEST_B1R2_TAG, peel: str = TEST_COMMIT, head: str = TEST_COMMIT
) -> RealTrustedB1R2Reader:
    """A REAL trusted reader whose git boundary observes a synthetic approved B1-R2 tag."""
    return RealTrustedB1R2Reader(
        git_runner=b1r2_git_runner(tag=tag, exists=True, peel=peel, head=head)
    )


@contextmanager
def approved_b1r2_governance(*, tag: str = TEST_B1R2_TAG, head: str = TEST_COMMIT):
    """TEST-ONLY: patch the mint's INTERNAL trust roots to simulate a FUTURE B1-R2 approval.

    B0CB-RR4-H1: the production mint exposes NO trust-root parameter. To exercise the
    future-positive path a test patches the two MODULE-LEVEL functions the mint resolves
    internally — ``current_approved_b1_r2_checkpoint`` (→ the synthetic approved identity)
    and ``_build_trusted_b1_r2_reader`` (→ a REAL reader over a scripted git boundary that
    observes that tag at ``head``). The production mint/driver/CLI signatures are unchanged
    and carry no test-only trust parameters; this patches internals only.
    """
    reader = b1r2_reader_ok(tag=tag, peel=head, head=head)
    with mock.patch.object(
        _authmint, "current_approved_b1_r2_checkpoint", return_value=tag
    ), mock.patch.object(
        _authmint, "_build_trusted_b1_r2_reader", return_value=reader
    ):
        yield


@contextmanager
def governance_expects_tag(tag: str = TEST_B1R2_TAG):
    """TEST-ONLY: patch ONLY the governance approved identity, leaving the REAL Git reader.

    Unlike ``approved_b1r2_governance`` (which also fakes the reader to observe the tag as
    PRESENT), this patches only ``current_approved_b1_r2_checkpoint`` → ``tag`` and keeps
    the default real-Git ``RealTrustedB1R2Reader``. Used to make "approved tag absent →
    fail closed" negatives PERMANENT: with a SYNTHETIC ``tag`` (e.g. ``TEST_B1R2_TAG``,
    which never becomes a real Git tag) the real reader always reports it absent, so the
    ``b1_r2_tag_not_observed_in_git`` refusal holds BEFORE and AFTER the real B1-R2
    checkpoint tag is created (checkpoint-lifecycle robustness).
    """
    with mock.patch.object(
        _authmint, "current_approved_b1_r2_checkpoint", return_value=tag
    ):
        yield


def mint_test_live_auth(
    *, run_id: str = TEST_RUN_ID, commit: str = TEST_COMMIT, tag: str = TEST_TAG
) -> LiveProviderRunAuthorization:
    """Mint a LiveProviderRunAuthorization from a MOCK grant + synthetic preflight (§14).

    This is a capability object for driving mocked backends — it authorizes NO real
    provider run (governance ``PN02_PROVIDER_RUN_AUTHORIZED`` stays NO). The B1-R2 gate
    passes only inside the ``approved_b1r2_governance`` patch (module-internal governance +
    reader); the real/default path stays fail-closed, and this calls the SAME production
    mint signature live code uses (no trust-root parameters).
    """
    ok, detail = verify_fixture_hash()
    fixture_hash = detail if ok else "UNVERIFIED"
    preflight = mint_real_preflight_authorization(
        gate0_passed=True, gate1_passed=True, fixture_hash=fixture_hash,
        run_id=run_id, runtime_count=3,
    )
    grant = frozen_test_grant(run_id=run_id, commit=commit, tag=tag)
    with approved_b1r2_governance(tag=grant.b1_r2_checkpoint, head=commit):
        return mint_live_provider_run_authorization(
            operator_grant=grant,
            real_preflight_auth=preflight,
            git_baseline_attestation=clean_git_baseline(commit=commit, tag=tag),
            observed_fixture_hash=fixture_hash,
        )


# --------------------------------------------------------------------------- #
# Full live-seams builder (the whole real path against mocks)
# --------------------------------------------------------------------------- #

@dataclass
class SeamBundle:
    seams: LiveB1Seams
    controller: FakeProcessController
    prober: FakeHealthProber
    preflight_runner: FakePreflightRunner
    corpus_db: FakeCorpusDB
    http_calls: Dict[str, int]
    base_url_to_notebook: Dict[str, str]


def build_live_seams(fx: FixturePN02, *, port_base: int = PORT_BASE) -> SeamBundle:
    """Build a full ``LiveB1Seams`` wired to mocks + the notebook-scoped mock transport.

    The provider-free preflight (B0CB-M1) publishes NO port, so it allocates none; only
    the 3 provider-bound execution runtimes call the port allocator (notebook order),
    so execution base_urls are ``port_base+0 .. port_base+2``.
    """
    controller = FakeProcessController()
    prober = FakeHealthProber()
    preflight_runner = FakePreflightRunner()
    corpus_db = FakeCorpusDB()
    http_calls: Dict[str, int] = {}

    notebook_ids = list(fx.notebook_ids)
    base_url_to_notebook = {
        f"http://127.0.0.1:{port_base + i}": nb
        for i, nb in enumerate(notebook_ids)
    }
    transport = build_mock_transport(fx, base_url_to_notebook, calls=http_calls)

    seams = LiveB1Seams(
        process_controller=controller,
        health_prober=prober,
        version_signal_reader=fake_version_signal_reader,
        port_allocator=DeterministicPortAllocator(port_base),
        storage_dir_allocator=fake_storage_dir_allocator,
        preflight_runner=preflight_runner,
        source_creator=corpus_db.source_creator(),
        reference_linker=corpus_db.reference_linker(),
        source_embedder=corpus_db.source_embedder(fx),
        notebook_record_ids={nb.notebook_id: nb.record_id for nb in fx.notebooks},
        query_embed_fn=make_query_embed_fn(fx),
        member_row_fetcher=corpus_db.member_row_fetcher(),
        model_attestor=make_model_attestor(),
        index_transport=transport,
        gd_transport=transport,
        delete_transport=transport,
        lightrag_api_key="dummy-test-key",
        present_secret_envs=frozenset({"OPENROUTER_API_KEY"}),
        corpus_teardown=corpus_db.teardown,
    )
    return SeamBundle(
        seams=seams,
        controller=controller,
        prober=prober,
        preflight_runner=preflight_runner,
        corpus_db=corpus_db,
        http_calls=http_calls,
        base_url_to_notebook=base_url_to_notebook,
    )
