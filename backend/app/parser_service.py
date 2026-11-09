"""Credential-free document parser reached only through a private Unix socket."""

import json
import os
import socket
import socketserver
import struct
import subprocess
import sys

from app.extraction import MAX_BYTES

SOCKET_PATH = "/app/parser-socket/extract.sock"
MAX_RESPONSE = 8 * 1024 * 1024
FORMATS = {b"D": "docx", b"P": "pdf"}


def read_exact(connection, size):
    parts = []
    while size:
        part = connection.recv(size)
        if not part:
            raise ConnectionError("Parser request ended early")
        parts.append(part)
        size -= len(part)
    return b"".join(parts)


def run_child(content, file_format):
    # The child inherits neither application credentials nor a network namespace.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.extraction", file_format],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
            env={
                "PATH": os.environ.get("PATH", ""),
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_code": "timeout"}
    if result.returncode or len(result.stdout) > MAX_RESPONSE:
        return {"ok": False, "error_code": "resource_limit"}
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("Invalid parser response")
        return payload
    except (ValueError, TypeError):
        return {"ok": False, "error_code": "parse_failed"}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(35)
        try:
            header = read_exact(self.request, 5)
            if header == b"PING!":
                self.request.sendall(b"PONG!")
                return
            file_format = FORMATS.get(header[:1])
            size = struct.unpack("!I", header[1:])[0]
            if not file_format or not 0 < size <= MAX_BYTES:
                payload = {"ok": False, "error_code": "file_limit"}
            else:
                payload = run_child(read_exact(self.request, size), file_format)
            encoded = json.dumps(payload, ensure_ascii=True).encode()
            if len(encoded) > MAX_RESPONSE:
                encoded = b'{"ok":false,"error_code":"resource_limit"}'
            self.request.sendall(struct.pack("!I", len(encoded)) + encoded)
        except (OSError, ValueError, struct.error):
            return


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8


def health():
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(2)
        connection.connect(SOCKET_PATH)
        connection.sendall(b"PING!")
        return read_exact(connection, 5) == b"PONG!"


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        sys.exit(0 if health() else 1)
    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    with Server(SOCKET_PATH, Handler) as server:
        os.chmod(SOCKET_PATH, 0o600)
        try:
            server.serve_forever()
        finally:
            os.unlink(SOCKET_PATH)


if __name__ == "__main__":
    main()
