"""Reusable HTTP/WebSocket registration server and TCP line-stream server.

Every VRphos script needs the same pair of servers: a FastAPI server that serves
constant CSVs and lets Unity devices register over a `/register` websocket, and a
raw TCP socket that streams comma-separated stimulus lines to a connected client.
This module owns their lifecycle (binding, background threads, reconnect handling,
clean shutdown) behind two small classes plus a `PhospheneServers` facade that
starts/stops both together.

Typical usage::

    from utils.network_servers import PhospheneServers
    from utils.dynaphos.utils import load_params

    params = load_params("config/params.yaml")
    servers = PhospheneServers.from_params(params, debug_mode=True)
    servers.start()
    try:
        while True:
            stim_pattern = ...  # compute per-frame stimulus
            servers.tcp.send_line(stim_pattern)
    finally:
        servers.stop()
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse


# ---------------- HTTP / WebSocket registration server ----------------

@dataclass
class HTTPServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    csv_folder: str = "./layout_data"
    log_level: str = "warning"


def build_registration_app(config: HTTPServerConfig) -> tuple[FastAPI, dict]:
    """Build the FastAPI app exposing `/constants/{filename}` and `/register`.

    Returns (app, device_registry); device_registry is filled in as devices call
    `/register` and maps device_id -> last seen IP.
    """
    app = FastAPI()
    device_registry: dict[str, str] = {}

    @app.get("/constants/{filename}")
    async def get_csv(filename: str):
        path = f"{config.csv_folder}/{filename}"
        try:
            return FileResponse(path, media_type="text/csv")
        except Exception:
            return {"error": "file not found"}

    @app.websocket("/register")
    async def websocket_register(websocket: WebSocket):
        await websocket.accept()
        while True:
            data = await websocket.receive_json()
            device_id = data.get("device_id")
            ip = websocket.client.host
            device_registry[device_id] = ip
            await websocket.send_json({"status": "ok", "ip": ip})
            print(f"Registered device {device_id} with IP {ip}")

    return app, device_registry


class HTTPRegistrationServer:
    """Runs the FastAPI registration server in a background thread."""

    def __init__(self, config: HTTPServerConfig):
        self.config = config
        self.app, self.device_registry = build_registration_app(config)
        self._uv_server = uvicorn.Server(
            uvicorn.Config(
                self.app, host=config.host, port=config.port, log_level=config.log_level
            )
        )
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._uv_server.run, daemon=True)
        self._thread.start()
        print(f"HTTP/WebSocket server listening on {self.config.port}")

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._uv_server.should_exit = True
        self._thread.join(timeout=timeout)
        self._thread = None


# ---------------- TCP line-stream server ----------------

@dataclass
class TCPServerConfig:
    host: str = "0.0.0.0"
    port: int = 9000
    # If True, send_line() never blocks on / requires a connected client (frames are
    # processed and dropped instead of streamed) - useful for local testing without Unity.
    debug_mode: bool = False


class TCPLineServer:
    """Background TCP server that accepts one client at a time and streams
    newline-terminated lines to it (e.g. comma-separated phosphene stimulus values).

    Accepting and reconnecting happen on a background thread, so the caller's frame
    loop just calls `send_line()` every iteration; it is a no-op whenever no client
    is currently connected.
    """

    def __init__(self, config: TCPServerConfig):
        self.config = config
        self._sock: Optional[socket.socket] = None
        self._conn: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def start(self) -> None:
        if self._accept_thread is not None:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.config.host, self.config.port))
        self._sock.listen(1)
        print(f"TCP stream server listening on {self.config.port}")
        self._stop_event.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break  # socket was closed by stop()
            print(f"TCP client connected from {addr}")
            with self._lock:
                self._conn = conn

    def send_line(self, values: Iterable) -> bool:
        """Send a comma-separated line built from `values`.

        Returns False if there is no connected client (or debug_mode is on, in which
        case sending is skipped entirely) and True once the line was written.
        """
        if self.config.debug_mode:
            return False
        with self._lock:
            conn = self._conn
        if conn is None:
            return False
        try:
            line = ",".join(map(str, values)) + "\n"
            conn.sendall(line.encode("utf-8"))
            return True
        except (ConnectionResetError, BrokenPipeError):
            print("⚠️ TCP client disconnected.")
            with self._lock:
                self._conn = None
            return False

    def wait_for_connection(self, poll_interval: float = 0.05) -> None:
        """Block until a client connects. No-op if debug_mode is on."""
        if self.config.debug_mode:
            return
        while not self.connected and not self._stop_event.is_set():
            time.sleep(poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)
            self._accept_thread = None


# ---------------- Combined facade ----------------

@dataclass
class ServersConfig:
    http: HTTPServerConfig = field(default_factory=HTTPServerConfig)
    tcp: TCPServerConfig = field(default_factory=TCPServerConfig)

    @classmethod
    def from_params(
        cls,
        params: dict,
        csv_folder: str = "./layout_data",
        debug_mode: bool = False,
    ) -> "ServersConfig":
        """Build a config from a loaded `params.yaml` dict (params["webserver"][...])."""
        webserver = params["webserver"]
        return cls(
            http=HTTPServerConfig(port=webserver["PORT_HTTP"], csv_folder=csv_folder),
            tcp=TCPServerConfig(port=webserver["PORT_TCP"], debug_mode=debug_mode),
        )


class PhospheneServers:
    """Owns both the HTTP registration server and the TCP stimulus stream server.

    Start/stop them together, and use `.tcp.send_line(...)` from a frame loop:

        servers = PhospheneServers.from_params(params, debug_mode=DEBUG_MODE)
        servers.start()
        ...
        servers.tcp.send_line(stim_pattern)
        ...
        servers.stop()
    """

    def __init__(self, config: ServersConfig):
        self.config = config
        self.http = HTTPRegistrationServer(config.http)
        self.tcp = TCPLineServer(config.tcp)

    @classmethod
    def from_params(cls, params: dict, **kwargs) -> "PhospheneServers":
        return cls(ServersConfig.from_params(params, **kwargs))

    @property
    def device_registry(self) -> dict:
        return self.http.device_registry

    def start(self) -> None:
        self.http.start()
        self.tcp.start()

    def stop(self) -> None:
        self.tcp.stop()
        self.http.stop()
