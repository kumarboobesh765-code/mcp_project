"""Loopback dashboard + MCP control bridge for the local paper trader.

Run via an MCP host over stdio. The companion dashboard is served at
http://127.0.0.1:8765 and only exposes simulation-only operations.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.server.fastmcp import FastMCP

HOST = "127.0.0.1"
PORT = int(os.environ.get("PAPER_TRADE_PORT", "8765"))
APP_FILE = Path(__file__).with_name("index.html")
COMMAND_TTL_SECONDS = 25
APP_HEARTBEAT_SECONDS = 15

mcp = FastMCP("solana-paper-terminal")
_lock = threading.RLock()
_state: dict = {}
_last_seen = 0.0
_next_id = 0
_commands: dict[int, dict] = {}
_waiters: dict[int, threading.Event] = {}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _app_is_open() -> bool:
    with _lock:
        return bool(_state) and time.monotonic() - _last_seen <= APP_HEARTBEAT_SECONDS


def _request_action(action: str, *, confirm: bool, **arguments) -> dict:
    if not confirm:
        return {
            "ok": False,
            "error": "No action queued. Set confirm=true only after the user explicitly requests this simulated trade or setting change.",
        }
    if not _app_is_open():
        return {"ok": False, "error": "Open the local paper-trading dashboard at http://127.0.0.1:8765 first."}

    global _next_id
    now = time.monotonic()
    with _lock:
        _next_id += 1
        command_id = _next_id
        event = threading.Event()
        command = {
            "id": command_id,
            "action": action,
            **arguments,
            "created_at": now,
            "expires_at": now + COMMAND_TTL_SECONDS,
            "status": "queued",
        }
        _commands[command_id] = command
        _waiters[command_id] = event

    if not event.wait(COMMAND_TTL_SECONDS + 3):
        with _lock:
            command = _commands.get(command_id, command)
            command["status"] = "expired"
            _waiters.pop(command_id, None)
            _commands.pop(command_id, None)
        return {"ok": False, "error": "Dashboard did not return a result before the paper command expired. No delayed execution will be accepted."}

    with _lock:
        completed = _commands.pop(command_id, command)
        _waiters.pop(command_id, None)
        result = completed.get("result")
    return result if isinstance(result, dict) else {"ok": False, "error": "Dashboard returned no valid result."}


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "PaperTradeLocal/1.0"

    def log_message(self, _format: str, *_args) -> None:
        # Keep the MCP stdio stream clean; diagnostics go to stderr.
        return

    def _same_origin(self) -> bool:
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        valid_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        if host not in valid_hosts:
            return False
        if origin is None:  # Native app or same-origin navigation.
            return True
        return origin in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

    def do_OPTIONS(self) -> None:
        # The dashboard is same-origin. Do not grant cross-origin access.
        self.send_error(403, "Cross-origin requests are not allowed")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            try:
                content = APP_FILE.read_bytes()
            except OSError:
                self.send_error(404, "Dashboard file not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if not self._same_origin():
            self.send_error(403, "Localhost only")
            return
        if parsed.path == "/api/state":
            with _lock:
                state = _state.copy()
                online = bool(state) and time.monotonic() - _last_seen <= APP_HEARTBEAT_SECONDS
            _json_response(self, 200, {"online": online, "state": state})
            return
        if parsed.path == "/api/commands":
            query = parse_qs(parsed.query)
            try:
                after = max(0, int(query.get("after", ["0"])[0]))
            except (TypeError, ValueError):
                after = 0
            now = time.monotonic()
            with _lock:
                pending = []
                for command_id, command in _commands.items():
                    if command_id <= after or command.get("status") != "queued":
                        continue
                    if now >= command["expires_at"]:
                        command["status"] = "expired"
                        command["result"] = {"ok": False, "error": "Command expired before delivery."}
                        waiter = _waiters.get(command_id)
                        if waiter:
                            waiter.set()
                        continue
                    pending.append({key: value for key, value in command.items() if key not in {"created_at", "expires_at", "result"}})
                    command["status"] = "delivered"
                cursor = _next_id
            _json_response(self, 200, {"commands": pending, "cursor": cursor})
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        if not self._same_origin():
            self.send_error(403, "Localhost only")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                _json_response(self, 413, {"error": "Invalid request size"})
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
        except (ValueError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "Expected a JSON object"})
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            state = payload.get("state")
            if not isinstance(state, dict) or state.get("mode") != "paper-only":
                _json_response(self, 400, {"error": "Expected paper-only dashboard state"})
                return
            with _lock:
                global _state, _last_seen
                _state = state
                _last_seen = time.monotonic()
            _json_response(self, 200, {"ok": True})
            return
        if parsed.path.startswith("/api/commands/") and parsed.path.endswith("/result"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 4 or parts[0:2] != ["api", "commands"] or parts[3] != "result":
                self.send_error(404, "Not found")
                return
            try:
                command_id = int(parts[2])
            except ValueError:
                self.send_error(400, "Invalid command id")
                return
            with _lock:
                command = _commands.get(command_id)
                if not command or command.get("status") not in {"queued", "delivered"}:
                    _json_response(self, 410, {"error": "Command is no longer active"})
                    return
                if time.monotonic() >= command["expires_at"]:
                    command["status"] = "expired"
                    waiter = _waiters.get(command_id)
                    if waiter:
                        waiter.set()
                    _json_response(self, 410, {"error": "Command expired"})
                    return
                result = payload.get("result")
                if not isinstance(result, dict):
                    _json_response(self, 400, {"error": "result must be an object"})
                    return
                command["result"] = result
                command["status"] = "completed"
                waiter = _waiters.get(command_id)
                if waiter:
                    waiter.set()
            _json_response(self, 200, {"ok": True})
            return
        self.send_error(404, "Not found")


def _json_tool_result(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
def get_paper_portfolio() -> str:
    """Read the local paper-trading portfolio, prices, positions, risk, and recent fills. This tool cannot access live wallet funds."""
    with _lock:
        state = _state.copy()
        fresh = bool(state) and time.monotonic() - _last_seen <= APP_HEARTBEAT_SECONDS
    if not fresh:
        return _json_tool_result({"ok": False, "error": "Paper dashboard is not open or has stopped responding. Open http://127.0.0.1:8765."})
    return _json_tool_result({"ok": True, **state})


@mcp.tool()
def paper_buy(symbol: str, amount_usd: float, confirm: bool = False) -> str:
    """Simulate a spot buy from the browser paper balance. Requires confirm=true after the user asks for this paper trade. Never sends an on-chain transaction."""
    return _json_tool_result(_request_action("paper_buy", confirm=confirm, symbol=symbol, amountUsd=amount_usd))


@mcp.tool()
def paper_sell(symbol: str, amount_usd: float, confirm: bool = False) -> str:
    """Simulate a spot sale worth approximately amount_usd at the latest paper mark. Requires confirm=true after the user asks. Never sends an on-chain transaction."""
    return _json_tool_result(_request_action("paper_sell", confirm=confirm, symbol=symbol, amountUsd=amount_usd))


@mcp.tool()
def set_paper_risk_level(level: int, confirm: bool = False) -> str:
    """Set paper order size cap to level percent of simulated equity (integer 1-20). Requires confirm=true after the user explicitly requests the setting change."""
    return _json_tool_result(_request_action("paper_set_risk", confirm=confirm, level=level))


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="paper-dashboard-http", daemon=True)
    thread.start()
    print(f"Paper trading dashboard: http://{HOST}:{PORT}", file=__import__("sys").stderr)
    try:
        mcp.run(transport="stdio")
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
