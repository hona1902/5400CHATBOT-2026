"""Provider-free tests for the GraphRAG-09H safe governed-evidence export.

No real provider / Docker / grant / DB / live execution: the governed executor is a
fake and every secret used is SYNTHETIC. These tests pin the secret-safe serializer,
the recursive scrub, the atomic fail-closed artifact write, and the one-shot export
control flow (preflight-before-execution, no executor retry on export failure).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import run_graphrag_09h_live as runner


# ----------------------------------------------------------------- fake result shapes
class _FakeVerdict:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


_PASS = _FakeVerdict("PASS_BOUNDED_LIVE_PROVENANCE_PLUMBING", "09H_LIVE_A")
_KILL = _FakeVerdict("KILL_SWITCH_TRIGGERED", "09H_LIVE_D")
_FAIL_SAFE = _FakeVerdict("LIVE_FAIL_SAFE", "09H_LIVE_C")


class _FakeResult:
    def __init__(self, verdict: _FakeVerdict, reason: str, evidence: dict) -> None:
        self.verdict = verdict
        self.reason = reason
        self.evidence = evidence


def _exec_factory(result, counter):
    async def _exec(deps, *, head_expected):
        counter["n"] += 1
        return result

    return _exec


def _run_export(result, out_path, counter=None, **kw):
    counter = counter if counter is not None else {"n": 0}
    return runner.execute_and_export(
        deps_factory=lambda: object(),
        run_validation=_exec_factory(result, counter),
        head_expected="deadbeef",
        evidence_output=str(out_path),
        exported_at="2026-01-01T00:00:00+00:00",
        **kw,
    )


# ----------------------------------------------------------------- PASS / FAIL export
def test_pass_result_evidence_export(tmp_path):  # §29
    out = tmp_path / "evidence_pass.json"
    res = _FakeResult(_PASS, "ok", {"graphrag_query_count": 1, "a3_product_visibility": 0})
    outcome = _run_export(res, out)
    assert outcome.exit_code == runner.EXIT_PASS
    assert outcome.export_ok is True
    assert Path(outcome.artifact_path) == out
    doc = json.loads(out.read_bytes().decode("utf-8"))
    assert doc["schema"] == "09H_live_run_evidence_v1"
    assert doc["verdict_value"] == "09H_LIVE_A"
    assert doc["evidence"]["graphrag_query_count"] == 1
    assert doc["evidence"]["a3_product_visibility"] == 0
    # reported sha/bytes match the artifact bytes exactly
    import hashlib

    raw = out.read_bytes()
    assert outcome.artifact_sha256 == hashlib.sha256(raw).hexdigest()
    assert outcome.artifact_bytes == len(raw)


def test_fail_result_evidence_export(tmp_path):  # §30
    out = tmp_path / "evidence_fail.json"
    res = _FakeResult(
        _FAIL_SAFE, "index_failed", {"stage": "INDEX", "proxy_index_calls": 1}
    )
    outcome = _run_export(res, out)
    assert outcome.exit_code == runner.EXIT_GOVERNED_FAIL  # not converted to PASS
    assert outcome.export_ok is True
    doc = json.loads(out.read_bytes().decode("utf-8"))
    assert doc["verdict_value"] == "09H_LIVE_C"
    assert doc["reason"] == "index_failed"
    assert doc["evidence"]["stage"] == "INDEX"


def test_run6_shaped_evidence_export_regression(tmp_path):  # §31
    out = tmp_path / "evidence_run6.json"
    evidence = {
        "canonical_chat": "PASS",
        "graphrag_query_count": 1,
        "final_product_provenance_source_ids": [
            "source:gr_pn02_a1",
            "source:gr_pn02_a2",
            "source:gr_pn02_a3",
        ],
        "expected_product_provenance_source_ids": [
            "source:gr_pn02_a1",
            "source:gr_pn02_a2",
        ],
        "proxy": {"index_calls": 3, "query_calls": 1},
        "checkpoint_message_count": 2,
    }
    res = _FakeResult(_KILL, "final_provenance_mismatch", evidence)
    outcome = _run_export(res, out)
    assert outcome.exit_code == runner.EXIT_GOVERNED_FAIL
    doc = json.loads(out.read_bytes().decode("utf-8"))
    ev = doc["evidence"]
    assert doc["reason"] == "final_provenance_mismatch"
    assert ev["canonical_chat"] == "PASS"
    assert ev["graphrag_query_count"] == 1
    assert ev["final_product_provenance_source_ids"] == [
        "source:gr_pn02_a1",
        "source:gr_pn02_a2",
        "source:gr_pn02_a3",
    ]
    assert ev["expected_product_provenance_source_ids"] == [
        "source:gr_pn02_a1",
        "source:gr_pn02_a2",
    ]
    assert ev["proxy"] == {"index_calls": 3, "query_calls": 1}
    assert ev["checkpoint_message_count"] == 2


# ----------------------------------------------------------------- secret redaction
def test_evidence_export_secret_redaction(tmp_path):  # §32 (synthetic secrets only)
    out = tmp_path / "evidence_secrets.json"
    evidence = {
        "top": "sk-or-v1-TOPLEAK0000000000",
        "nested": {"inner": "value sk-TESTLEAK12345678 trailing"},
        "as_list": ["Bearer BEARERLEAKTOKEN123", "clean"],
        "as_tuple": ("prefix sk-or-v1-TUPLELEAK999", 7),
        "exc_like": "RuntimeError: auth failed with sk-or-v1-EXCLEAK55555",
    }
    res = _FakeResult(_PASS, "ok", evidence)
    _run_export(res, out)
    raw = out.read_bytes()
    for leak in (
        b"TOPLEAK",
        b"TESTLEAK12345678",
        b"BEARERLEAKTOKEN123",
        b"TUPLELEAK999",
        b"EXCLEAK55555",
    ):
        assert leak not in raw, leak
    # structure preserved, secrets replaced with the marker
    doc = json.loads(raw.decode("utf-8"))
    assert doc["evidence"]["top"] == "[REDACTED_SECRET]"
    assert doc["evidence"]["as_tuple"][1] == 7
    assert doc["evidence"]["as_list"][1] == "clean"


def test_sensitive_key_redaction(tmp_path):  # §33
    out = tmp_path / "evidence_keys.json"
    evidence = {
        "api_key": "sk-or-v1-KEYLEAK",
        "authorization": "Bearer AUTHLEAK",
        "token": "TOKENLEAK",
        "secret": "SECRETLEAK",
        "password": "PASSLEAK",
        "credential": "CREDLEAK",
        "x-pbp-control": "CONTROLLEAK",
        "nested": {"api-key": "NESTEDKEYLEAK"},
        "safe_count": 5,
    }
    res = _FakeResult(_PASS, "ok", evidence)
    _run_export(res, out)
    raw = out.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    ev = doc["evidence"]
    for k in (
        "api_key",
        "authorization",
        "token",
        "secret",
        "password",
        "credential",
        "x-pbp-control",
    ):
        assert ev[k] == "[REDACTED]", k
    assert ev["nested"]["api-key"] == "[REDACTED]"
    assert ev["safe_count"] == 5
    for leak in (
        b"KEYLEAK",
        b"AUTHLEAK",
        b"TOKENLEAK",
        b"SECRETLEAK",
        b"PASSLEAK",
        b"CREDLEAK",
        b"CONTROLLEAK",
        b"NESTEDKEYLEAK",
    ):
        assert leak not in raw, leak


def test_secret_value_pattern_redaction(tmp_path):  # §34
    out = tmp_path / "evidence_patterns.json"
    evidence = {
        "a": "Bearer abcDEF123456.token",
        "b": "sk-or-v1-PATTERNLEAK0000",
        "c": "sk-PATTERNGENERIC1234567",
    }
    res = _FakeResult(_PASS, "ok", evidence)
    _run_export(res, out)
    raw = out.read_bytes()
    for leak in (b"abcDEF123456", b"PATTERNLEAK", b"PATTERNGENERIC1234567"):
        assert leak not in raw, leak


def test_unsupported_object_repr_secret(tmp_path):  # §35 (provisioning-repr defect class)
    class _LeakyModel:
        def __repr__(self) -> str:
            return "sk-or-v1-FAKESECRETFROMREPR999"

    out = tmp_path / "evidence_unsupported.json"
    res = _FakeResult(_PASS, "ok", {"model": _LeakyModel(), "ok": True})
    _run_export(res, out)
    raw = out.read_bytes()
    assert b"FAKESECRETFROMREPR999" not in raw
    doc = json.loads(raw.decode("utf-8"))
    assert doc["evidence"]["model"] == "[UNSUPPORTED_TYPE:_LeakyModel]"
    assert doc["evidence"]["ok"] is True


def test_environment_not_autocaptured(tmp_path, monkeypatch):  # §36
    monkeypatch.setenv("FAKE_LEAK_ENV", "sk-or-v1-ENVLEAK00000000")
    out = tmp_path / "evidence_env.json"
    res = _FakeResult(_PASS, "ok", {"only": "this"})
    _run_export(res, out)
    raw = out.read_bytes()
    assert b"ENVLEAK" not in raw
    assert b"FAKE_LEAK_ENV" not in raw


# ----------------------------------------------------------------- serializer surface
def test_scrub_supports_all_evidence_types():  # §13
    enum_sample = runner._PASS_VERDICT_VALUE  # str constant
    value = {
        "none": None,
        "bool": True,
        "int": 3,
        "float": 2.5,
        "str": "plain",
        "list": [1, "two", False],
        "tuple": (1, 2),
        "set": {1},
        "dict": {"k": "v"},
    }
    scrubbed = runner.scrub_evidence(value)
    # JSON-serializable without error and shape preserved for safe types
    doc = json.loads(runner.serialize_evidence_artifact({"evidence": scrubbed}).decode())
    ev = doc["evidence"]
    assert ev["none"] is None and ev["bool"] is True and ev["int"] == 3
    assert ev["float"] == 2.5 and ev["str"] == "plain"
    assert ev["list"] == [1, "two", False]
    assert ev["tuple"] == [1, 2]  # tuple -> list
    assert ev["dict"] == {"k": "v"}
    assert isinstance(enum_sample, str)


def test_serialized_artifact_is_utf8_json_no_bom(tmp_path):  # §7
    out = tmp_path / "evidence_fmt.json"
    _run_export(_FakeResult(_PASS, "ok", {"x": 1}), out)
    raw = out.read_bytes()
    assert raw[:3] != b"\xef\xbb\xbf"  # no BOM
    json.loads(raw.decode("utf-8"))  # valid UTF-8 JSON


# ----------------------------------------------------------------- one-shot control flow
def test_preflight_fail_if_exists_before_execution(tmp_path):  # §44/§45
    out = tmp_path / "exists.json"
    out.write_bytes(b"{}")  # precreate -> fail-closed
    counter = {"n": 0}
    with pytest.raises(FileExistsError):
        _run_export(_FakeResult(_PASS, "ok", {}), out, counter=counter)
    assert counter["n"] == 0  # executor NEVER called (no one-shot burned)
    assert out.read_bytes() == b"{}"  # prior evidence untouched


def test_preflight_fail_missing_parent_before_execution(tmp_path):  # §45
    out = tmp_path / "missing_dir" / "evidence.json"
    counter = {"n": 0}
    with pytest.raises(FileNotFoundError):
        _run_export(_FakeResult(_PASS, "ok", {}), out, counter=counter)
    assert counter["n"] == 0


def test_post_execution_export_failure_no_retry(tmp_path):  # §23/§46
    out = tmp_path / "evidence_writefail.json"
    counter = {"n": 0}

    def _boom_writer(data, output_path, *, overwrite=False):
        raise OSError("disk full (synthetic sk-or-v1-SHOULDNOTLEAK)")

    outcome = _run_export(
        _FakeResult(_KILL, "final_provenance_mismatch", {"k": 1}),
        out,
        counter=counter,
        _writer=_boom_writer,
    )
    assert counter["n"] == 1  # executed exactly once, not retried
    assert outcome.export_ok is False
    assert outcome.exit_code == runner.EXIT_EVIDENCE_EXPORT_FAIL
    # governed verdict/reason preserved; error text scrubbed of the synthetic secret
    assert outcome.verdict_value == "09H_LIVE_D"
    assert outcome.reason == "final_provenance_mismatch"
    assert "SHOULDNOTLEAK" not in (outcome.export_error or "")
    assert not out.exists()  # nothing partial left behind


def test_atomic_write_leaves_no_temp(tmp_path):  # §20
    out = tmp_path / "evidence_atomic.json"
    _run_export(_FakeResult(_PASS, "ok", {"x": 1}), out)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".evidence-")]
    assert leftovers == []
    assert out.exists()


# ----------------------------------------------------------------- path-safety matrix
def test_relative_evidence_output_rejected():  # §4/§34.2
    # A relative destination is rejected deterministically, never reinterpreted against
    # CWD/repo/home/temp.
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output("relative_evidence.json")


def test_approved_external_evidence_path_accepted(tmp_path):  # §12/§34.1/§34.9
    out = tmp_path / "ok_evidence.json"
    runner.preflight_evidence_output(str(out))  # no raise
    assert not out.exists()  # preflight creates nothing


def test_repo_root_destination_rejected():  # §7/§34.3
    out = runner._CANONICAL_REPO_ROOT / "__evidence_should_reject.json"
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output(str(out))
    assert not out.exists()


def test_tracked_subdir_destination_rejected():  # §7/§34.4
    out = runner._CANONICAL_REPO_ROOT / "api" / "__evidence_should_reject.json"
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output(str(out))
    assert not out.exists()


def test_tests_subdir_destination_rejected():  # §7
    out = runner._CANONICAL_REPO_ROOT / "tests" / "__evidence_should_reject.json"
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output(str(out))
    assert not out.exists()


def test_dot_git_destination_rejected():  # §9/§34.5
    out = runner._CANONICAL_REPO_ROOT / ".git" / "run.json"
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output(str(out))
    assert not out.exists()


def test_nested_dot_git_destination_rejected():  # §9/§34.6
    out = runner._CANONICAL_REPO_ROOT / ".git" / "objects" / "run.json"
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output(str(out))
    assert not out.exists()


def test_symlink_parent_rejected(tmp_path):  # §10/§34.7
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    link_dir = tmp_path / "symlink_parent"
    try:
        os.symlink(str(real_dir), str(link_dir), target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted on host: {exc}")
    if not link_dir.is_symlink():  # pragma: no cover - host-dependent
        pytest.skip("symlink not created as a link on host")
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output(str(link_dir / "evidence.json"))


def test_windows_junction_parent_rejected(tmp_path):  # §10/§34.8
    if sys.platform != "win32":
        pytest.skip("junction test is Windows-specific")
    real_dir = tmp_path / "real_junction_target"
    real_dir.mkdir()
    link_dir = tmp_path / "junction_parent"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_dir), str(real_dir)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not link_dir.exists():  # pragma: no cover - host-dependent
        pytest.skip(f"could not create junction: {proc.stderr or proc.stdout}")
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.preflight_evidence_output(str(link_dir / "evidence.json"))


def test_missing_parent_rejected(tmp_path):  # §6/§34.10
    out = tmp_path / "missing_dir" / "evidence.json"
    with pytest.raises(FileNotFoundError):
        runner.preflight_evidence_output(str(out))


def test_preexisting_final_artifact_rejected(tmp_path):  # §15/§34.11
    out = tmp_path / "exists.json"
    out.write_bytes(b"PRIOR")
    with pytest.raises(FileExistsError):
        runner.preflight_evidence_output(str(out))
    assert out.read_bytes() == b"PRIOR"


def test_direct_writer_cannot_bypass_safety():  # §14/§34.12
    # A direct call to the writer (bypassing CLI preflight) still rejects a repo path.
    target = runner._CANONICAL_REPO_ROOT / ".git" / "__writer_bypass.json"
    with pytest.raises(runner.EvidenceOutputPolicyError):
        runner.write_evidence_artifact(b'{"x":1}', str(target))
    assert not target.exists()


def test_fail_if_exists_race_regression(tmp_path):  # §16/§18/§34.13
    # Another creator wins the final name between preflight and publication: export fails
    # (no-clobber), the competitor file is untouched, and the executor is NOT retried.
    out = tmp_path / "race.json"
    counter = {"n": 0}

    def _racing_writer(data, output_path, *, overwrite=False):
        Path(output_path).write_bytes(b"COMPETITOR")  # appears after preflight passed
        return runner.write_evidence_artifact(data, output_path, overwrite=overwrite)

    outcome = _run_export(
        _FakeResult(_PASS, "ok", {"x": 1}), out, counter=counter, _writer=_racing_writer
    )
    assert counter["n"] == 1  # executed exactly once, not retried
    assert outcome.export_ok is False
    assert outcome.exit_code == runner.EXIT_EVIDENCE_EXPORT_FAIL
    assert out.read_bytes() == b"COMPETITOR"  # never overwritten


def test_link_publication_is_no_clobber_at_fs_boundary(tmp_path, monkeypatch):  # §16
    # Prove the FINAL publication itself (not just preflight) is no-clobber: skip
    # validation (simulating a target that appears AFTER validation) and confirm the
    # os.link publication refuses to overwrite and leaves no temp residue.
    out = tmp_path / "boundary.json"
    out.write_bytes(b"WINNER")
    monkeypatch.setattr(
        runner, "_validate_evidence_destination", lambda p, *, overwrite=False: Path(str(p))
    )
    with pytest.raises(FileExistsError):
        runner.write_evidence_artifact(b'{"x":1}', str(out))
    assert out.read_bytes() == b"WINNER"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".evidence-")]
    assert leftovers == []


def test_repo_containment_not_prefix_fooled(tmp_path):  # §8 component-safe, not prefix
    # A sibling dir whose name merely shares the repo-root prefix is NOT "inside" the repo.
    sibling = Path(str(runner._CANONICAL_REPO_ROOT) + "-evidence-sibling")
    # Only assert the containment predicate (do not create anything on disk).
    assert runner._path_within(runner._CANONICAL_REPO_ROOT, sibling) is False
    assert runner._path_within(runner._CANONICAL_REPO_ROOT, runner._CANONICAL_REPO_ROOT) is True
    assert (
        runner._path_within(runner._CANONICAL_REPO_ROOT, runner._CANONICAL_REPO_ROOT / "api" / "x")
        is True
    )
