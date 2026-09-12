"""Fusion Studio Local HTTP & SSE Server.

Serves the Angular frontend bundle and provides local-only JSON REST and SSE
endpoints bound strictly to 127.0.0.1.
"""

import http.server
import json
import mimetypes
import os
import queue
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from fusion_agent.ui_bridge.handler import UIBridgeHandler
from fusion_agent.ui_bridge.protocol import BridgeEvent, BridgeRequest, UICommand, UIEvent


class StudioHTTPServer(http.server.ThreadingHTTPServer):
    """Threaded HTTP server holding handler and event subscriber state."""

    def __init__(self, server_address, RequestHandlerClass, project_root: Optional[str] = None):
        super().__init__(server_address, RequestHandlerClass)
        self.subscribers: List[queue.Queue] = []
        self.subscribers_lock = threading.Lock()
        self.dist_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "fusion-studio"
            / "dist"
            / "fusion-studio"
            / "browser"
        )
        self.handler = UIBridgeHandler(
            project_root=project_root,
            event_sink=self.broadcast_event,
        )

    def broadcast_event(self, event: BridgeEvent) -> None:
        """Push a structured event to all active SSE subscribers."""
        payload = f"data: {json.dumps(event.to_dict())}\n\n"
        with self.subscribers_lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.put_nowait(payload)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.subscribers.remove(q)


class StudioRequestHandler(http.server.BaseHTTPRequestHandler):
    """Handles static files, API requests, and SSE streams for Fusion Studio."""

    server: StudioHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress noisy default request logging."""
        pass

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # 1. API: Event Stream (SSE)
        if path == "/api/events" or path == "/api/task/events":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            ev_queue: queue.Queue = queue.Queue(maxsize=100)
            with self.server.subscribers_lock:
                self.server.subscribers.append(ev_queue)

            try:
                # Send initial ping
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = ev_queue.get(timeout=15.0)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass
            finally:
                with self.server.subscribers_lock:
                    if ev_queue in self.server.subscribers:
                        self.server.subscribers.remove(ev_queue)
            return

        # 2. API: Project Status
        if path == "/api/status" or path == "/api/project":
            resp = self.server.handler.handle_request(
                BridgeRequest(id="1", command=UICommand.GET_PROJECT_STATUS.value)
            )
            self._send_json(200, resp.to_dict())
            return

        # 3. API: List Files
        if path == "/api/files":
            subpath = qs.get("subpath", [""])[0]
            resp = self.server.handler.handle_request(
                BridgeRequest(id="2", command=UICommand.LIST_FILES.value, params={"subpath": subpath})
            )
            self._send_json(200, resp.to_dict())
            return

        # 4. API: Read File
        if path == "/api/file":
            filepath = qs.get("path", [""])[0] or qs.get("filepath", [""])[0]
            resp = self.server.handler.handle_request(
                BridgeRequest(id="3", command=UICommand.READ_FILE.value, params={"filepath": filepath})
            )
            self._send_json(200, resp.to_dict())
            return

        # 5. API: Get Diff
        if path == "/api/task/diff" or path == "/api/diff":
            resp = self.server.handler.handle_request(
                BridgeRequest(id="4", command=UICommand.GET_DIFF.value)
            )
            self._send_json(200, resp.to_dict())
            return

        # 6. API: Get Test Results
        if path == "/api/task/tests":
            resp = self.server.handler.handle_request(
                BridgeRequest(id="5", command=UICommand.GET_TEST_RESULTS.value)
            )
            self._send_json(200, resp.to_dict())
            return

        # 7. API: Get Review Findings
        if path == "/api/task/review":
            resp = self.server.handler.handle_request(
                BridgeRequest(id="6", command=UICommand.GET_REVIEW_FINDINGS.value)
            )
            self._send_json(200, resp.to_dict())
            return

        # 8. API: History
        if path == "/api/history":
            limit = int(qs.get("limit", [20])[0])
            resp = self.server.handler.handle_request(
                BridgeRequest(id="7", command=UICommand.LIST_TASKS.value, params={"limit": limit})
            )
            self._send_json(200, resp.to_dict())
            return

        # 9. API: Providers & Doctor
        if path == "/api/providers":
            prov_resp = self.server.handler.handle_request(
                BridgeRequest(id="8", command=UICommand.GET_PROVIDER_STATUS.value)
            )
            doc_resp = self.server.handler.handle_request(
                BridgeRequest(id="9", command=UICommand.RUN_DOCTOR.value)
            )
            self._send_json(
                200,
                {
                    "success": True,
                    "data": {
                        "providers": prov_resp.data.get("providers", []) if prov_resp.data else [],
                        "doctor": doc_resp.data if doc_resp.data else {},
                    },
                },
            )
            return

        # 10. Static File Serving (from Angular dist)
        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_body_json()

        # 1. API: Open Project
        if path == "/api/project/open":
            project_path = body.get("path", "")
            resp = self.server.handler.handle_request(
                BridgeRequest(id="10", command=UICommand.OPEN_PROJECT.value, params={"path": project_path})
            )
            self._send_json(200, resp.to_dict())
            return

        # 2. API: Init Project
        if path == "/api/project/init":
            resp = self.server.handler.handle_request(
                BridgeRequest(id="11", command=UICommand.INIT_PROJECT.value, params=body)
            )
            self._send_json(200, resp.to_dict())
            return

        # 3. API: Write File
        if path == "/api/file":
            filepath = body.get("filepath", "") or body.get("path", "")
            content = body.get("content", "")
            resp = self.server.handler.handle_request(
                BridgeRequest(
                    id="12",
                    command=UICommand.WRITE_FILE.value,
                    params={"filepath": filepath, "content": content},
                )
            )
            self._send_json(200, resp.to_dict())
            return

        # 4. API: Start Task
        if path == "/api/task/start" or path == "/api/task/run":
            instruction = body.get("instruction", "") or body.get("prompt", "")

            # Check clean status synchronously before starting thread
            if not self.server.handler._is_git_clean():
                self._send_json(
                    400,
                    {
                        "success": False,
                        "error": {
                            "code": "DIRTY_REPOSITORY",
                            "message": (
                                "Repository contains uncommitted changes. Fusion requires a clean repository "
                                "before autonomous execution. Commit or stash your changes before running this task."
                            ),
                        },
                    },
                )
                return

            def _worker():
                self.server.handler.handle_request(
                    BridgeRequest(
                        id="task-run",
                        command=UICommand.START_TASK.value,
                        params={"instruction": instruction},
                    )
                )

            worker_thread = threading.Thread(target=_worker, daemon=True)
            worker_thread.start()

            self._send_json(200, {"success": True, "data": {"status": "started", "instruction": instruction}})
            return

        # 5. API: Resume Task
        if path == "/api/task/resume":
            task_id = body.get("task_id", "")

            def _resume_worker():
                self.server.handler.handle_request(
                    BridgeRequest(
                        id="task-resume",
                        command=UICommand.RESUME_TASK.value,
                        params={"task_id": task_id},
                    )
                )

            worker_thread = threading.Thread(target=_resume_worker, daemon=True)
            worker_thread.start()

            self._send_json(200, {"success": True, "data": {"status": "resumed", "task_id": task_id}})
            return

        # 6. API: Approve Task
        if path == "/api/task/approve":
            task_id = body.get("task_id", "")
            resp = self.server.handler.handle_request(
                BridgeRequest(id="13", command=UICommand.APPROVE_TASK.value, params={"task_id": task_id})
            )
            self._send_json(200, resp.to_dict())
            return

        # 7. API: Reject Task
        if path == "/api/task/reject":
            task_id = body.get("task_id", "")
            resp = self.server.handler.handle_request(
                BridgeRequest(id="14", command=UICommand.REJECT_TASK.value, params={"task_id": task_id})
            )
            self._send_json(200, resp.to_dict())
            return

        # 8. API: Save Settings
        if path == "/api/settings":
            resp = self.server.handler.handle_request(
                BridgeRequest(id="15", command=UICommand.SAVE_CONFIG.value, params=body)
            )
            self._send_json(200, resp.to_dict())
            return

        self._send_json(404, {"error": "Not Found"})

    def _serve_static(self, req_path: str) -> None:
        """Serve built Angular assets or fallback to index.html."""
        clean = req_path.lstrip("/").replace("\\", "/")
        if not clean or clean == "/":
            clean = "index.html"

        file_path = (self.server.dist_dir / clean).resolve()
        if not file_path.is_file() or not str(file_path).startswith(str(self.server.dist_dir)):
            # SPA fallback
            file_path = self.server.dist_dir / "index.html"

        if not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Fusion Studio web bundle not found. Run 'npm run build' inside fusion-studio.")
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        content = file_path.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def start_studio_server(
    project_root: Optional[str] = None,
    port: int = 4200,
    open_browser: bool = True,
) -> None:
    """Start the local-only Fusion Studio HTTP and SSE server."""
    host = "127.0.0.1"
    resolved_root = str(Path(project_root or ".").resolve())

    # Find open port if 4200 is in use
    server = None
    for try_port in [port, port + 1, port + 2, 5000, 8080, 8000]:
        try:
            server = StudioHTTPServer((host, try_port), StudioRequestHandler, project_root=resolved_root)
            port = try_port
            break
        except OSError:
            continue

    if not server:
        raise RuntimeError(f"Could not bind Fusion Studio server to {host} on any tested port.")

    url = f"http://{host}:{port}"
    print("=" * 70)
    print("  FUSION STUDIO — AI-Native Coding Workspace")
    print("=" * 70)
    print(f"  Project Root: {resolved_root}")
    print(f"  Local Server: {url}")
    print(f"  Bound To:     {host} (Local only, no external network access)")
    print("  Press Ctrl+C to stop.")
    print("=" * 70)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Fusion Studio server...")
        server.shutdown()
