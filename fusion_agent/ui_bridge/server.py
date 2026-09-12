"""Standard input/output JSON-RPC style server for Fusion UI Bridge.

Reads newline-delimited JSON commands from stdin, executes via UIBridgeHandler,
and writes structured responses and events as newline-delimited JSON to stdout.
"""

import json
import sys
import threading
from typing import Optional

from fusion_agent.ui_bridge.handler import UIBridgeHandler
from fusion_agent.ui_bridge.protocol import BridgeEvent, BridgeRequest, BridgeResponse


class UIBridgeServer:
    """Stdio server running the UI Bridge JSON protocol loop."""

    def __init__(self, initial_project_root: Optional[str] = None):
        self.lock = threading.Lock()
        self.handler = UIBridgeHandler(
            project_root=initial_project_root,
            event_sink=self._send_event,
        )
        self._running = False

    def _send_event(self, event: BridgeEvent) -> None:
        """Write a formatted asynchronous event line to stdout."""
        payload = json.dumps(event.to_dict())
        with self.lock:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()

    def _send_response(self, response: BridgeResponse) -> None:
        """Write a formatted synchronous response line to stdout."""
        payload = json.dumps(response.to_dict())
        with self.lock:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()

    def handle_line(self, line: str) -> None:
        """Parse and handle a single line of input."""
        line = line.strip()
        if not line:
            return

        try:
            data = json.loads(line)
            req = BridgeRequest.from_dict(data)
        except Exception as exc:
            err_resp = BridgeResponse(
                id=None,
                success=False,
                error={"code": "INVALID_JSON", "message": f"Failed to parse JSON: {exc}"},
            )
            self._send_response(err_resp)
            return

        # Execute handler in a worker thread if long-running task, else inline
        if req.command in ("START_TASK", "RESUME_TASK"):
            worker = threading.Thread(
                target=self._execute_async,
                args=(req,),
                daemon=True,
            )
            worker.start()
        else:
            resp = self.handler.handle_request(req)
            self._send_response(resp)

    def _execute_async(self, req: BridgeRequest) -> None:
        resp = self.handler.handle_request(req)
        self._send_response(resp)

    def run(self) -> None:
        """Main blocking stdio event loop."""
        self._running = True
        try:
            for line in sys.stdin:
                if not self._running:
                    break
                self.handle_line(line)
        except (KeyboardInterrupt, SystemExit):
            pass


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else None
    server = UIBridgeServer(initial_project_root=root)
    server.run()


if __name__ == "__main__":
    main()
