"""Single-writer recorder for actual dispatch-ledger segments and stage references.

No fee totals, dispatches, retries, evaluation data or legacy requests are
invented here. Immutable seals preserve the exact supplied ledger JSON values;
later response settlement appends a revision rather than changing old evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import sys

from contractedge.contracts.artifacts import (
    ArtifactRef, CallRef, LedgerSeal, StageReceipt, digest, identity, index, relative_path,
)
from contractedge.contracts.validation import canonical_digest


_SETTLEMENT_FIELDS = {
    "response_content", "response_metadata", "provider_error_metadata", "error", "elapsed_seconds",
    "dispatch_state",
}


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _clone(value):
    return json.loads(_bytes(value))


def _coerce(cls, value):
    if isinstance(value, cls):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("Expected " + cls.__name__ + " or its serialized mapping")
    payload = dict(value)
    version = payload.pop("schema_version", cls.__dataclass_fields__["schema_version"].default)
    if version != cls.__dataclass_fields__["schema_version"].default:
        raise ValueError("Unsupported reference schema version")
    return cls(**payload)


def _label(value):
    identity(value)
    if value in {".", ".."} or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) is None:
        raise ValueError("Stage and attempt labels must be restricted filename tokens")
    return value


def preserve_error(primary, record_action):
    """Run recording; an existing primary exception always remains authoritative."""
    try:
        return record_action()
    except Exception as error:
        if primary is None:
            raise
        try:
            previous = tuple(getattr(primary, "recording_errors", ()))
            primary.recording_errors = previous + (type(error).__name__ + ": " + str(error),)
        except Exception:
            pass
        return None


class RunRecorder:
    def __init__(self, root, *, run_id: str, input_digest: str):
        identity(run_id)
        digest(input_digest)
        candidate = Path(root).absolute()
        if any(path.is_symlink() for path in (candidate, *candidate.parents)):
            raise ValueError("Recorder root and ancestors must not be symlinks")
        candidate.mkdir(parents=True, exist_ok=True)
        if not candidate.is_dir():
            raise ValueError("Recorder root must be a directory")
        self.root, self.run_id, self.input_digest = candidate.resolve(), run_id, input_digest
        self._write_once("journal/run.identity.json", {
            "schema_version": "ce.run_recorder.v1", "run_id": run_id, "input_digest": input_digest,
        })

    def commit_transition(self, expected_revision, decision):
        from contractedge.orchestration.state import commit_transition
        return commit_transition(self, expected_revision, decision)

    def _path(self, name):
        relative_path(name)
        path = self.root / name
        current = self.root
        for part in Path(name).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Recorder paths must not traverse symlinks")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Recorder path escapes its root")
        return path

    def _parent(self, name):
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._path(name)

    @staticmethod
    def _sync_directory(directory):
        if os.name != "nt":
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _write_once(self, name, value):
        path, content = self._parent(name), _bytes(value)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError("Immutable recorder file differs: " + name)
            return
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        self._sync_directory(path.parent)

    def _write_pending(self, name, value):
        path, content = self._parent(name), _bytes(value)
        descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._sync_directory(path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self, name):
        path = self._path(name)
        if not path.is_file():
            raise ValueError("Missing recorder artifact: " + name)
        return json.loads(path.read_bytes())

    def artifact_ref(self, name, kind, *, snapshot_id=None):
        path = self._path(name)
        if not path.is_file():
            raise ValueError("Artifact is not an existing regular file: " + name)
        return ArtifactRef(kind, name, hashlib.sha256(path.read_bytes()).hexdigest(),
                           snapshot_id, self.input_digest)

    def capture_artifact(self, name, kind, *, snapshot_id=None):
        """Retain exact bytes before a compatibility view can be overwritten."""
        source = self._path(name)
        if not source.is_file():
            raise ValueError("Artifact is not an existing regular file: " + name)
        content = source.read_bytes()
        target = "journal/artifacts/" + hashlib.sha256(content).hexdigest() + "/" + name
        path = self._parent(target)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError("Immutable artifact bytes differ: " + target)
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._sync_directory(path.parent)
        return self.artifact_ref(target, kind, snapshot_id=snapshot_id)

    def capture_value(self, value, kind, *, snapshot_id=None):
        content = _bytes(value)
        name = "journal/values/" + hashlib.sha256(content).hexdigest() + ".json"
        self._write_once(name, value)
        return self.artifact_ref(name, kind, snapshot_id=snapshot_id)

    def receipt_ref(self, receipt):
        return self.artifact_ref(receipt.relative_path, "STAGE_RECEIPT",
                                 snapshot_id=receipt.snapshot_id)

    def verify_artifact(self, reference):
        reference = _coerce(ArtifactRef, reference)
        if reference.input_digest != self.input_digest:
            raise ValueError("Artifact belongs to another method input")
        actual = self.artifact_ref(reference.relative_path, reference.kind,
                                   snapshot_id=reference.snapshot_id)
        if actual != reference:
            raise ValueError("Artifact bytes changed: " + reference.relative_path)
        return reference

    @staticmethod
    def _pending_name(segment_index):
        index(segment_index)
        return "journal/ledger/segment-" + str(segment_index).zfill(6) + ".inflight.json"

    @staticmethod
    def _revision_name(segment_index, revision, *, metadata=False):
        index(segment_index)
        index(revision)
        return ("journal/ledger/segment-" + str(segment_index).zfill(6)
                + "/revision-" + str(revision).zfill(6) + (".seal.json" if metadata else ".json"))

    def _pending(self, segment_index):
        state = self._read(self._pending_name(segment_index))
        if (state.get("run_id") != self.run_id or state.get("input_digest") != self.input_digest
                or state.get("segment_index") != segment_index):
            raise ValueError("Pending ledger identity differs")
        index(state.get("first_call_index"))
        if not isinstance(state.get("calls"), list) or any(not isinstance(c, dict) for c in state["calls"]):
            raise ValueError("Pending ledger must contain actual call mappings")
        return state

    @staticmethod
    def _request_digest(call):
        value = call.get("request_digest")
        request = call.get("request")
        if "request_digest" not in call and isinstance(request, Mapping):
            value = canonical_digest(request)
        digest(value, optional=True)
        if value is not None and isinstance(request, Mapping) and value != canonical_digest(request):
            raise ValueError("Ledger request digest does not match its actual request")
        return value

    @staticmethod
    def _transport_digest(call):
        value = call.get("transport_body_sha256")
        if value is None and isinstance(call.get("transport_request"), Mapping):
            value = call["transport_request"].get("body_sha256")
        digest(value, optional=True)
        return value

    def _refs(self, calls, segment_index, first_call_index, *, seal_digest=None, seal_path=None, revision=None):
        return tuple(CallRef(self.run_id, segment_index, first_call_index + offset,
            self._request_digest(call), self._transport_digest(call),
            seal_digest, seal_path, revision) for offset, call in enumerate(calls))

    def _load_seal(self, name):
        wire = self._read(name)
        payload = dict(wire)
        payload["artifact_ref"] = _coerce(ArtifactRef, payload["artifact_ref"])
        payload["call_refs"] = tuple(_coerce(CallRef, value) for value in payload["call_refs"])
        seal = _coerce(LedgerSeal, payload)
        if (seal.run_id != self.run_id or seal.input_digest != self.input_digest
                or name != self._revision_name(seal.segment_index, seal.revision, metadata=True)):
            raise ValueError("Sealed ledger identity differs")
        expected_path = self._revision_name(seal.segment_index, seal.revision)
        if seal.artifact_ref.relative_path != expected_path:
            raise ValueError("Sealed ledger path differs")
        self.verify_artifact(seal.artifact_ref)
        calls = self._read(expected_path)
        if not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls):
            raise ValueError("Sealed ledger must contain actual call mappings")
        expected = self._refs(calls, seal.segment_index, seal.first_call_index,
            seal_digest=seal.artifact_ref.sha256, seal_path=expected_path, revision=seal.revision)
        if seal.call_refs != expected:
            raise ValueError("Sealed CallRefs differ from the actual immutable revision")
        return seal

    def _seals(self, segment_index):
        directory = self._path("journal/ledger/segment-" + str(segment_index).zfill(6))
        names = sorted(directory.glob("revision-*.seal.json")) if directory.is_dir() else []
        seals = [self._load_seal(path.relative_to(self.root).as_posix()) for path in names]
        if [seal.revision for seal in seals] != list(range(len(seals))):
            raise ValueError("Sealed ledger revisions are not contiguous")
        return seals

    @staticmethod
    def _validate_extension(previous, current):
        if len(previous) > len(current):
            raise ValueError("Ledger range was truncated")
        for old, new in zip(previous, current):
            old_request = {k: v for k, v in old.items() if k not in _SETTLEMENT_FIELDS}
            new_request = {k: v for k, v in new.items() if k not in _SETTLEMENT_FIELDS}
            if old_request != new_request:
                raise ValueError("Previously recorded dispatch identity changed")

    def update_ledger(self, calls, *, segment_index=0, first_call_index=0):
        """Persist exactly an actual on_update ledger and return stable inflight refs."""
        index(segment_index)
        index(first_call_index)
        calls = _clone(calls)
        if not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls):
            raise ValueError("Actual ledger must be a list of call mappings")
        references = self._refs(calls, segment_index, first_call_index)
        seals = self._seals(segment_index)  # Every immutable revision must still verify.
        if seals:
            if seals[-1].first_call_index != first_call_index:
                raise ValueError("Sealed ledger range was rebound")
            self._validate_extension(self._read(seals[-1].artifact_ref.relative_path), calls)
        pending_name = self._pending_name(segment_index)
        if self._path(pending_name).exists():
            previous = self._pending(segment_index)
            if previous["first_call_index"] != first_call_index or len(previous["calls"]) > len(calls):
                raise ValueError("Ledger range was rebound or truncated")
            self._validate_extension(previous["calls"], calls)
        directory = self._path("journal/ledger")
        if directory.exists():
            for other in directory.glob("segment-*.inflight.json"):
                if other.name == Path(pending_name).name:
                    continue
                state = self._read(other.relative_to(self.root).as_posix())
                left, right = state["first_call_index"], state["first_call_index"] + len(state["calls"])
                if max(left, first_call_index) < min(right, first_call_index + len(calls)):
                    raise ValueError("Actual call indices overlap another ledger segment")
        self._write_pending(pending_name, {"schema_version": "ce.inflight_ledger.v1",
            "run_id": self.run_id, "input_digest": self.input_digest,
            "segment_index": segment_index, "first_call_index": first_call_index, "calls": calls})
        return references

    def seal_ledger(self, *, segment_index=0):
        state = self._pending(segment_index)
        seals = self._seals(segment_index)
        if seals:
            if seals[-1].first_call_index != state["first_call_index"]:
                raise ValueError("Sealed ledger range was rebound")
            self._validate_extension(self._read(seals[-1].artifact_ref.relative_path), state["calls"])
        if seals and self._path(seals[-1].artifact_ref.relative_path).read_bytes() == _bytes(state["calls"]):
            return seals[-1]
        revision = len(seals)
        name = self._revision_name(segment_index, revision)
        self._write_once(name, state["calls"])
        artifact = self.artifact_ref(name, "DISPATCH_LEDGER_SEGMENT")
        references = self._refs(state["calls"], segment_index, state["first_call_index"],
            seal_digest=artifact.sha256, seal_path=name, revision=revision)
        seal = LedgerSeal(self.run_id, self.input_digest, segment_index, revision,
                          state["first_call_index"], artifact, references)
        self._write_once(self._revision_name(segment_index, revision, metadata=True), asdict(seal))
        return seal

    def verify_call(self, reference):
        reference = _coerce(CallRef, reference)
        if reference.run_id != self.run_id:
            raise ValueError("Call belongs to another run")
        if reference.ledger_segment_digest is not None:
            seal = self._load_seal(self._revision_name(reference.segment_index,
                                                      reference.ledger_revision, metadata=True))
            if reference not in seal.call_refs:
                raise ValueError("CallRef does not match its named immutable ledger revision")
        else:
            pending = self._pending(reference.segment_index)
            if reference not in self._refs(pending["calls"], reference.segment_index,
                                           pending["first_call_index"]):
                raise ValueError("Inflight CallRef does not match the actual ledger")
        return reference

    @staticmethod
    def _stage_unsigned(receipt):
        return {key: value for key, value in asdict(receipt).items() if key != "receipt_digest"}

    def _load_stage(self, name, *, stage, attempt_id, revision):
        wire = self._read(name)
        recorded_digest = wire.pop("receipt_digest")
        if canonical_digest(wire) != recorded_digest:
            raise ValueError("Previous stage receipt digest differs")
        wire["receipt_digest"] = recorded_digest
        for field in ("input_refs", "output_refs", "projection_refs"):
            wire[field] = tuple(_coerce(ArtifactRef, value) for value in wire[field])
        wire["call_refs"] = tuple(_coerce(CallRef, value) for value in wire["call_refs"])
        wire["issues"] = tuple(wire["issues"])
        receipt = _coerce(StageReceipt, wire)
        if (receipt.run_id != self.run_id or receipt.input_digest != self.input_digest
                or receipt.stage != stage or receipt.attempt_id != attempt_id
                or receipt.revision != revision or receipt.relative_path != name):
            raise ValueError("Existing stage receipt belongs to another identity or revision")
        expected_name = ("journal/stages/" + stage + "/" + attempt_id
                         + "/revision-" + str(revision).zfill(6) + ".json")
        if name != expected_name:
            raise ValueError("Stage receipt revisions are not contiguous")
        return receipt

    def record_stage(self, stage, *, attempt_id, snapshot_id, status, input_refs=(),
                     output_refs=(), projection_refs=(), call_refs=(), issues=()):
        """Append an immutable attempt revision; no status is inferred from files."""
        _label(stage)
        _label(attempt_id)
        inputs = tuple(self.verify_artifact(value) for value in input_refs)
        outputs = tuple(self.verify_artifact(value) for value in output_refs)
        projections = tuple(self.verify_artifact(value) for value in projection_refs)
        references = tuple(self.verify_call(value) for value in call_refs)
        keys = [(ref.segment_index, ref.absolute_call_index) for ref in references]
        if len(keys) != len(set(keys)):
            raise ValueError("A stage must not duplicate one actual call under multiple references")
        directory_name = "journal/stages/" + stage + "/" + attempt_id
        directory = self._path(directory_name)
        previous_paths = sorted(directory.glob("revision-*.json")) if directory.is_dir() else []
        revision = len(previous_paths)
        previous_receipts = [self._load_stage(path.relative_to(self.root).as_posix(),
            stage=stage, attempt_id=attempt_id, revision=number)
            for number, path in enumerate(previous_paths)]
        if previous_receipts:
            previous = previous_receipts[-1]
            candidate = StageReceipt(self.run_id, self.input_digest, stage, attempt_id,
                snapshot_id, status, inputs, outputs, projections, references,
                tuple(_clone(list(issues))), previous.revision, previous.relative_path)
            if previous.receipt_digest == canonical_digest(self._stage_unsigned(candidate)):
                return previous
        name = directory_name + "/revision-" + str(revision).zfill(6) + ".json"
        receipt = StageReceipt(self.run_id, self.input_digest, stage, attempt_id,
            snapshot_id, status, inputs, outputs, projections, references,
            tuple(_clone(list(issues))), revision, name)
        receipt = replace(receipt, receipt_digest=canonical_digest(self._stage_unsigned(receipt)))
        self._write_once(name, asdict(receipt))
        return receipt


class ProviderJournal:
    """Production hooks; call identities come only from BudgetedProvider.calls.

    A resumed compatibility trace is retained in its own legacy segment. The
    continuing budget owns one new segment with absolute indices after it.
    Nothing in this class reserves tokens, dispatches, or sums fees.
    """
    def __init__(self, recorder, *, prior_calls=()):
        self.recorder = recorder
        self.provider = None
        self.first_call_index = len(prior_calls)
        self.segment_index = 1 if prior_calls else 0
        self.dispatches = []
        self.latest = {}
        self.primary_error = None
        if prior_calls:
            recorder.update_ledger(list(prior_calls), segment_index=0)
            recorder.seal_ledger(segment_index=0)

    def attach(self, provider):
        from contractedge.model.budget import BudgetedProvider
        if not isinstance(provider, BudgetedProvider):
            raise ValueError("Stage hooks require the actual BudgetedProvider ledger")
        self.provider = provider
        self.update(provider.calls)
        return self

    def update(self, calls):
        return self.recorder.update_ledger(calls, segment_index=self.segment_index,
                                           first_call_index=self.first_call_index)

    def callback(self, calls, legacy_update):
        # BudgetedProvider invokes this in finally while the provider's original
        # exception is active. Preserve it even if either persistence path fails.
        # A correction may be requested while its caller is still inside an
        # unrelated validation except block. Only the actual failed dispatch's
        # settled error record authorizes preserving an active exception.
        primary = sys.exc_info()[1] if calls and calls[-1].get('error') else None
        preserve_error(primary, lambda: legacy_update(calls))
        preserve_error(primary, lambda: self.update(calls))

    def position(self):
        return len(self.provider.calls) if self.provider is not None else 0

    def refs(self, call_start=0):
        if self.provider is None:
            return ()
        self.update(self.provider.calls)
        return self.recorder.seal_ledger(segment_index=self.segment_index).call_refs[call_start:]

    def capture(self, name, kind, *, snapshot_id=None):
        return self.recorder.capture_artifact(name, kind, snapshot_id=snapshot_id)

    def record(self, stage, *, attempt_id, snapshot_id, status, call_start=0,
               input_refs=(), output_refs=(), projection_refs=(), issues=()):
        receipt = self.recorder.record_stage(stage, attempt_id=attempt_id,
            snapshot_id=snapshot_id, status=status, input_refs=input_refs,
            output_refs=output_refs, projection_refs=projection_refs,
            call_refs=self.refs(call_start), issues=issues)
        self.latest[stage] = self.recorder.receipt_ref(receipt)
        return receipt

    def record_dispatch(self, stage, *, attempt_id, snapshot_id, call_start, status):
        receipt = self.record("dispatch", attempt_id=attempt_id, snapshot_id=snapshot_id,
            status=status, call_start=call_start,
            issues=({"stage": stage, "scope": "actual BudgetedProvider ledger; no cost projection"},))
        self.dispatches.append((stage, receipt))
        return receipt

    def final_front(self, trace, *, attempt_id, dispatch_start, input_refs=(), projection_refs=()):
        source = self.recorder.capture_value(trace.to_wire(), "FRONT_TRACE", snapshot_id=trace.snapshot_id)
        recent = self.dispatches[dispatch_start:]
        previous = tuple(input_refs)
        for stage, value, prefixes in (
                ("diagnosis", trace.diagnosis, ("DIAGNOSIS",)),
                ("planning", trace.changeset, ("SEMANTIC_CHANGESET", "CHANGESET_REVISION"))):
            produced = tuple((name, receipt) for name, receipt in recent if name.startswith(prefixes))
            refs = produced[-1][1].call_refs if produced else ()
            request_refs = tuple(self.recorder.receipt_ref(receipt) for _, receipt in produced)
            output = (() if value is None else (self.recorder.capture_value(value,
                "FINAL_DIAGNOSIS" if stage == "diagnosis" else "FINAL_SEMANTIC_PLAN",
                snapshot_id=trace.snapshot_id),))
            receipt = self.recorder.record_stage(stage, attempt_id=attempt_id,
                snapshot_id=trace.snapshot_id,
                status="PRODUCED" if value is not None else "NOT_PRODUCED",
                input_refs=previous + request_refs, output_refs=output,
                projection_refs=projection_refs, call_refs=refs,
                issues=(() if produced or value is None else ({"code": "LEGACY_CALL_LINK_INCOMPLETE"},)))
            self.latest[stage] = self.recorder.receipt_ref(receipt)
            previous = (self.latest[stage],)
        receipt = self.record("front", attempt_id=attempt_id, snapshot_id=trace.snapshot_id,
            status=trace.status, call_start=self.position(), input_refs=previous,
            output_refs=(source,), projection_refs=projection_refs)
        return receipt
