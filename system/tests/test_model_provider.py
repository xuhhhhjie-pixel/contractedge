import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from contractedge.model.provider import ModelRequest, OllamaNativeProvider


class OllamaNativeProviderTests(unittest.TestCase):
    def test_sends_request_schema_to_native_chat_and_preserves_usage(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["request"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps({
                    "model": "fixture-model", "done": True, "done_reason": "stop",
                    "prompt_eval_count": 5, "eval_count": 7,
                    "message": {"role": "assistant", "content": '{"answer":"ok"}'},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        schema = {"type": "object", "properties": {"answer": {"type": "string"}},
                  "required": ["answer"], "additionalProperties": False}
        request = ModelRequest(model="fixture-model", prompt_version="diagnosis.v1.1.11",
            messages=({"role": "system", "content": "Return JSON."},), max_output_tokens=256,
            temperature=0.0, output_schema=schema)
        response = OllamaNativeProvider(f"http://127.0.0.1:{server.server_port}", 2).complete_json(request)

        self.assertEqual(captured["path"], "/api/chat")
        self.assertEqual(captured["request"]["format"], schema)
        self.assertFalse(captured["request"]["stream"])
        self.assertEqual(captured["request"]["options"]["num_predict"], 256)
        self.assertEqual(json.loads(response.content), {"answer": "ok"})
        self.assertEqual(response.metadata["usage"], {"prompt_tokens": 5, "completion_tokens": 7})
        self.assertEqual(response.metadata["finish_reason"], "stop")


if __name__ == "__main__":
    unittest.main()
