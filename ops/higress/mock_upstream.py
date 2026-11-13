"""Disposable VM acceptance backend. Never include in the business deployment.

Uses only Python's standard library; has no ERP client or model credentials.
The mode file lets acceptance checks simulate provider failure and recovery.
"""
import json
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = Path(__file__).with_name("mock-mode.txt")
COUNTS = {"calls": 0, "failed": 0, "successful": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.send_json(200, COUNTS)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        COUNTS["calls"] += 1
        mode = STATE.read_text().strip() if STATE.exists() else "healthy"
        if mode == "failed":
            COUNTS["failed"] += 1
            return self.send_json(503, {"error": {"message": "fixture unavailable", "type": "server_error"}})
        COUNTS["successful"] += 1
        self.send_json(200, {"id": "fixture", "object": "chat.completion", "created": int(time.time()),
            "model": "deepseek-flash", "choices": [{"index": 0, "message": {"role": "assistant",
            "content": "fixture recovered"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 18089), Handler).serve_forever()
