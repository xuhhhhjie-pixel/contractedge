import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from contractedge.experiments import m1

CONFIGS = Path(__file__).resolve().parents[1] / 'configs'


def write_packet(tmp_path):
    path = tmp_path / 'packet.json'
    path.write_text(json.dumps({'documents': [
        {'id': 'task', 'first_line': 10, 'text': 'Forward the name.\nKeep the labels.\n'}
    ]}))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_future_method_and_live_configurations():
    baseline = m1.load_config(CONFIGS / 'm1-live-baseline.json')
    method = m1.load_config(CONFIGS / 'm1-evidence-roles.json')
    control = m1.load_config(CONFIGS / 'm1-ordinary-control.json')
    assert baseline['m1'] == 'disabled'
    assert (CONFIGS / baseline['live_profile']).is_file()
    assert method['model'] == control['model'] == 'deepseek-flash'
    a = m1.prompt(control)
    b = m1.prompt(method)
    assert b.startswith(a + '\n')
    assert b[len(a):].strip().count('\n') == 0


def test_shared_packet_handoff_and_technical_capture(tmp_path):
    packet, digest = write_packet(tmp_path)
    row = {'requirement': 'forward the name', 'type': 'change', 'conditions': 'when given',
           'relation': 'name propagates', 'evidence_status': 'clear', 'sources': [
               {'document': 'task', 'start_line': 20, 'end_line': 20, 'quote': 'Forward the name.'}
           ]}
    pending = {**row, 'evidence_status': 'pending', 'sources': [], 'relation': None}
    clear_bad = {**row, 'sources': row['sources'] + [
        {'document': 'task', 'start_line': 10, 'end_line': 10, 'quote': 'invented'}]}
    content = json.dumps({'requirements': [row, pending, clear_bad], 'unresolved_questions': ['Which default?']})
    body = json.dumps({'model': 'deepseek-flash', 'choices': [
        {'message': {'content': content}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}).encode()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            for chunk in (body[:47], body[47:]):
                self.wfile.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n')
            self.wfile.write(b'0\r\n\r\n')

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            out = tmp_path / 'run'
            command = [sys.executable, '-m', 'contractedge.experiments.m1', 'run',
                       '--config', str(CONFIGS / 'm1-evidence-roles.json'),
                       '--packet', str(packet), '--packet-sha256', digest, '--out', str(out),
                       '--fixture-origin', f'http://127.0.0.1:{server.server_port}']
            result = subprocess.run(command, capture_output=True)
            assert result.returncode == 0, result.stderr.decode()
            assert subprocess.run(command, capture_output=True).returncode != 0
        finally:
            server.shutdown()
            thread.join()

    assert len(requests) == 1
    assert requests[0] == (out / 'request.body').read_bytes()
    assert (out / 'response.body').read_bytes() == body
    handoff = json.loads((out / 'handoff.json').read_text())
    assert handoff == {'clear_requirements': [row], 'pending_requirements': [pending],
                       'unresolved_questions': ['Which default?']}
    mechanics = json.loads((out / 'mechanical.json').read_text())
    assert mechanics['citation_locations']['0'][0]['resolved']['start_line'] == 10
    assert mechanics['rows'][2]['status'] == 'REJECTED'
    assert json.loads((out / 'result.json').read_text())['usage_origin'] == 'SYNTHETIC'
