"""A fake SoftUEBridge on a free local port, for transport and shutdown tests (no editor needed).

Modes for POST (tool calls and shutdown):
  ok           valid JSON-RPC reply (tool result {"success": true, "output": <output>}, shutdown result {})
  hang         read the request, never answer (until the server stops)
  close        read the request, close without a byte
  rst          accept, then reset the connection without reading
  midbody      headers with Content-Length, then a cut body
  headersonly  headers with Content-Length, no body
  empty200     200 with an empty body
  emptyjson200 200 with body {}
  emptyresult  JSON-RPC reply with result {}
  emptytext    tool result whose text is "{}"
GET /bridge answers {"pid", "project_dir"} unless get_mode is "hang" (busy editor) or "close".
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time


class FakeBridge:
    def __init__(self, mode: str = "ok", *, pid: int | None = None, project_dir: str | None = None,
                 output: str = "hello\n", get_mode: str = "ok") -> None:
        self.mode = mode
        self.get_mode = get_mode
        self.pid = pid
        self.project_dir = project_dir
        self.output = output
        self.requests: list[tuple[str, str]] = []  # (verb, JSON-RPC method or path)
        self._stop = threading.Event()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "FakeBridge":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._sock.close()

    @property
    def methods(self) -> list[str]:
        return [m for verb, m in self.requests if verb == "POST"]

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _read(self, conn: socket.socket) -> tuple[str, bytes]:
        conn.settimeout(5)
        buf = b""
        while b"\r\n\r\n" not in buf:
            data = conn.recv(65536)
            if not data:
                return "", b""
            buf += data
        head, _, body = buf.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        while len(body) < length:
            data = conn.recv(65536)
            if not data:
                break
            body += data
        return head.split(b" ")[0].decode(), body

    @staticmethod
    def _send(conn: socket.socket, body: bytes, length: int | None = None) -> None:
        length = len(body) if length is None else length
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
            + f"Content-Length: {length}\r\n\r\n".encode() + body
        )

    def _wait_stop(self) -> None:
        while not self._stop.wait(0.1):
            pass

    def _handle(self, conn: socket.socket) -> None:
        try:
            if self.mode == "rst":
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                self.requests.append(("RST", ""))
                return
            verb, body = self._read(conn)
            if verb == "GET":
                self.requests.append(("GET", "/bridge"))
                if self.get_mode == "hang":
                    self._wait_stop()
                    return
                if self.get_mode == "close":
                    return
                info = {"name": "soft-ue-bridge", "running": True}
                if self.pid is not None:
                    info["pid"] = self.pid
                if self.project_dir is not None:
                    info["project_dir"] = self.project_dir
                self._send(conn, json.dumps(info).encode())
                return
            try:
                request = json.loads(body or b"{}")
            except ValueError:
                request = {}
            method = request.get("method", "")
            self.requests.append(("POST", method))
            rid = request.get("id", "1")
            mode = self.mode
            if mode == "hang":
                self._wait_stop()
            elif mode == "close":
                pass
            elif mode == "midbody":
                reply = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"content": []}}).encode()
                self._send(conn, reply[:20], len(reply))
                time.sleep(0.2)
            elif mode == "headersonly":
                self._send(conn, b"", 196)
                time.sleep(0.2)
            elif mode == "empty200":
                self._send(conn, b"")
            elif mode == "emptyjson200":
                self._send(conn, b"{}")
            elif mode == "emptyresult" or method == "shutdown":
                self._send(conn, json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}).encode())
            elif mode == "emptytext":
                result = {"content": [{"type": "text", "text": "{}"}]}
                self._send(conn, json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode())
            else:
                text = json.dumps({"success": True, "output": self.output})
                result = {"content": [{"type": "text", "text": text}]}
                self._send(conn, json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode())
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
