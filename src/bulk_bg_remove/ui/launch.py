"""Start the local UI: pick a free port, mint a token, open the browser."""

from __future__ import annotations

import secrets
import socket
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import uvicorn

from ..bulk.events import Event, EventSink
from .server import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def serve(
    *,
    echo: EventSink | None = None,
    initial_path: Path | None = None,
    defaults: dict[str, str] | None = None,
    open_browser: bool = True,
    announce: EventSink | None = None,
) -> str:
    """Run the UI server until Ctrl+C. Returns the URL it served."""
    port = _free_port()
    token = secrets.token_urlsafe(24)
    query = {"t": token, **(defaults or {})}
    if initial_path is not None:
        query["p"] = str(initial_path.expanduser().resolve())
    url = f"http://127.0.0.1:{port}/?{urlencode(query)}"
    app = create_app(token, allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"}, echo=echo)
    if announce is not None:
        announce(Event("ui_ready", {"url": url}))
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return url
