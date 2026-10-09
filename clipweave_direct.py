"""ClipWeave-owned TLS/auth boundary; never disables Vast proxy authentication."""
import hmac
import json
import os
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 32 * 1024 * 1024


def make_handler(secret, health, submit):
    if len(secret) < 32:
        raise ValueError("CLIPWEAVE_WORKER_SECRET must contain at least 32 characters")

    class DirectHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log request credentials or signed delivery URLs.

        def respond(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            return hmac.compare_digest(self.headers.get("Authorization", "").encode(),
                                       ("Bearer " + secret).encode())

        def do_GET(self):
            if not self.authorized():
                self.respond(401, {"error": "Unauthorized"})
                return
            if self.path != "/clipweave/health":
                self.respond(404, {"error": "Not found"})
                return
            self.respond(200, health())

        def do_POST(self):
            if not self.authorized():
                self.respond(401, {"error": "Unauthorized"})
                return
            if self.path != "/clipweave/generate":
                self.respond(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    self.respond(413, {"error": "Invalid request size"})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or not isinstance(payload.get("input"), dict):
                    raise ValueError("Expected an input object")
                status, result = submit(payload)
                self.respond(status, result)
            except (ValueError, json.JSONDecodeError):
                self.respond(400, {"error": "Invalid render request"})
            except Exception:
                self.respond(503, {"error": "Worker cannot accept the request"})

    return DirectHandler


def start_direct_server(health, submit):
    secret = os.environ.get("CLIPWEAVE_WORKER_SECRET", "")
    cert = os.environ.get("CLIPWEAVE_WORKER_TLS_CERT", "")
    key = os.environ.get("CLIPWEAVE_WORKER_TLS_KEY", "")
    if not secret and not cert and not key:
        return  # Legacy templates remain on the authenticated Vast proxy.
    handler = make_handler(secret, health, submit)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Credentials are materialized privately and removed once loaded into TLS.
    with tempfile.TemporaryDirectory() as directory:
        for name, value in [("cert.pem", cert), ("key.pem", key)]:
            path = os.path.join(directory, name)
            with open(path, "w") as file:
                file.write(value.replace("\\n", "\n"))
            os.chmod(path, 0o600)
        context.load_cert_chain(os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem"))
    server = ThreadingHTTPServer(("0.0.0.0", 18290), handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("[ClipWeave] Authenticated HTTPS API listening on port 18290", flush=True)
