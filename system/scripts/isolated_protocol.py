"""Finite JSON RPC with durable, fail-closed side-effect identities.

The journal belongs to the trusted coordinator, outside the method namespace.
It intentionally offers no retry of an uncertain provider invocation. Only a
previously persisted DONE result may be recovered after transport loss.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import struct
import threading
import uuid

MAX_FRAME = 8 * 1024 * 1024
ALLOWED_OPERATIONS = frozenset({
    'model.complete', 'model.overhead', 'model.metadata', 'budget.summary',
    'public.prepare', 'public.observe', 'public.export',
})


class ProtocolError(RuntimeError):
    """Only stable codes, never underlying provider or filesystem errors."""


class DispatchCancelled(ProtocolError):
    """The coordinator cancelled before entering a side-effect handler."""


def _json_bytes(value, *, canonical=False):
    try:
        result = json.dumps(value, ensure_ascii=True, allow_nan=False,
                            sort_keys=canonical, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        raise ProtocolError('INVALID_JSON') from None
    if len(result) > MAX_FRAME:
        raise ProtocolError('FRAME_TOO_LARGE')
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError('INVALID_JSON')
        result[key] = value
    return result


def _decode(data):
    def invalid_constant(value):
        raise ProtocolError('INVALID_JSON')
    try:
        value = json.loads(data, object_pairs_hook=_unique_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError('INVALID_JSON') from None
    if not isinstance(value, dict):
        raise ProtocolError('INVALID_JSON')
    return value


def _read_exact(sock, size):
    chunks = []
    while size:
        data = sock.recv(size)
        if not data:
            raise EOFError('local channel closed')
        chunks.append(data)
        size -= len(data)
    return b''.join(chunks)


def receive_frame(sock):
    size, = struct.unpack('!I', _read_exact(sock, 4))
    if not 0 < size <= MAX_FRAME:
        raise ProtocolError('FRAME_TOO_LARGE')
    return _decode(_read_exact(sock, size))


def send_frame(sock, value):
    body = _json_bytes(value)
    sock.sendall(struct.pack('!I', len(body)) + body)


class Journal:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ProtocolError('INVALID_JOURNAL')

    @contextmanager
    def _locked(self):
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(MAX_FRAME + 1)
        if len(data) > MAX_FRAME:
            raise ProtocolError('INVALID_JOURNAL')
        return _decode(data)

    def _write(self, path, value):
        data = _json_bytes(value)
        temporary = self.root / ('.write-' + uuid.uuid4().hex)
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            parent = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            temporary.unlink(missing_ok=True)

    def execute(self, request_id, identity, effect):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', request_id):
            raise ProtocolError('INVALID_REQUEST_ID')
        digest = hashlib.sha256(_json_bytes(identity, canonical=True)).hexdigest()
        path = self.root / (request_id + '.json')
        with self._locked():
            if path.exists() or path.is_symlink():
                previous = self._read(path)
                if previous['identity_sha256'] != digest:
                    raise ProtocolError('IDENTITY_MISMATCH')
                if previous['status'] == 'DONE':
                    return previous['result']
                if previous['status'] == 'NOT_DISPATCHED':
                    raise ProtocolError('RUN_CLOSED')
                raise ProtocolError('OUTCOME_UNKNOWN')
            # A crash may have left no explicit error marker. Read the durable
            # pending records, rather than relying on in-memory request state.
            if any(self._read(p).get('status') != 'DONE' for p in self.root.glob('*.json')):
                raise ProtocolError('OUTCOME_UNKNOWN')
            record = {'identity_sha256': digest, 'op': identity.get('op'), 'status': 'PENDING'}
            self._write(path, record)
            try:
                result = effect()
                if not isinstance(result, dict):
                    raise ProtocolError('INVALID_RESULT')
                record = dict(record, status='DONE', result=result)
                self._write(path, record)
            except DispatchCancelled:
                self._write(path, dict(record, status='NOT_DISPATCHED', error='RUN_CLOSED'))
                raise ProtocolError('RUN_CLOSED') from None
            except Exception:
                # Neither guess whether the service billed nor expose its error.
                # Keep PENDING and forbid all new side effects in this session.
                raise ProtocolError('OUTCOME_UNKNOWN') from None
            return result


class JsonRpcServer:
    def __init__(self, path, token, journal, handlers, *, sensitive_values=()):
        if not token or not set(handlers) <= ALLOWED_OPERATIONS:
            raise ProtocolError('INVALID_SERVER_CONFIG')
        self.path = Path(path)
        self.token = token
        self.journal = journal
        self.handlers = handlers
        self.sensitive = tuple(str(s) for s in (*sensitive_values, token) if s)
        self._server = None
        self._closing = threading.Event()

    def cancel_pending(self):
        """Prevent queued handlers from starting; in-flight outcomes stay durable."""
        self._closing.set()

    def _check_sensitive(self, value):
        # Compare decoded strings too: JSON escaping must not bypass the check.
        if isinstance(value, str):
            if any(secret in value for secret in self.sensitive):
                raise ProtocolError('SENSITIVE_DATA')
        elif isinstance(value, dict):
            for key, item in value.items():
                self._check_sensitive(key)
                self._check_sensitive(item)
        elif isinstance(value, list):
            for item in value:
                self._check_sensitive(item)

    def dispatch(self, request):
        if set(request) != {'token', 'request_id', 'op', 'payload'}:
            raise ProtocolError('INVALID_REQUEST')
        auth = request['token']
        if not isinstance(auth, str) or not hmac.compare_digest(auth.encode(), self.token.encode()):
            raise ProtocolError('UNAUTHORIZED')
        op, payload = request['op'], request['payload']
        if not isinstance(op, str) or op not in self.handlers or op not in ALLOWED_OPERATIONS:
            raise ProtocolError('OPERATION_DENIED')
        if not isinstance(payload, dict):
            raise ProtocolError('INVALID_REQUEST')
        self._check_sensitive(payload)
        self._check_sensitive(request['request_id'])
        def effect():
            if self._closing.is_set():
                raise DispatchCancelled('RUN_CLOSED')
            result = self.handlers[op](payload)
            self._check_sensitive(result)
            return result
        semantic_payload = dict(payload)
        if op == 'model.complete':
            semantic_payload.pop('timeout_seconds', None)
        return self.journal.execute(request['request_id'], {'op': op, 'payload': semantic_payload}, effect)

    def __enter__(self):
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(15)
                try:
                    result = owner.dispatch(receive_frame(self.request))
                    response = {'ok': True, 'result': result}
                except ProtocolError as error:
                    response = {'ok': False, 'error': {'code': str(error)}}
                except Exception:
                    response = {'ok': False, 'error': {'code': 'CHANNEL_ERROR'}}
                try:
                    send_frame(self.request, response)
                except (OSError, EOFError, ProtocolError):
                    pass  # DONE is already durable even if this client vanished.
        self._server = socketserver.ThreadingUnixStreamServer(str(self.path), Handler)
        os.chmod(self.path, 0o600)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.cancel_pending()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
        self.path.unlink(missing_ok=True)


class RpcClient:
    def __init__(self, path, token, *, timeout=300):
        self.path, self.token, self.timeout = str(path), token, timeout

    def call(self, op, payload, *, request_id=None):
        request = {'token': self.token, 'request_id': request_id or uuid.uuid4().hex,
                   'op': op, 'payload': payload}
        for attempt in range(2):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(self.timeout)
                    sock.connect(self.path)
                    send_frame(sock, request)
                    response = receive_frame(sock)
                if response.get('ok') is not True:
                    code = response.get('error', {}).get('code')
                    # Never echo arbitrary peer error data.
                    if not isinstance(code, str) or not re.fullmatch('[A-Z_]+', code):
                        code = 'CHANNEL_ERROR'
                    raise ProtocolError(code)
                if not isinstance(response.get('result'), dict):
                    raise ProtocolError('INVALID_RESULT')
                return response['result']
            except (OSError, EOFError):
                if attempt:
                    raise ProtocolError('TRANSPORT_UNCERTAIN') from None
        raise AssertionError('unreachable')
