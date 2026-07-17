#!/usr/bin/env python3
"""Ejecuta la aplicación web de Blackboard Backup.

Uso:
    python run.py

Abre automáticamente el navegador en http://localhost:5000
"""

import os
import socket
import threading
import webbrowser


def _available_port(start=5000):
    for port in range(start, start + 20):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No hay un puerto local disponible")

if __name__ == "__main__":
    port = int(os.environ.get("BB_PORT", _available_port()))
    url = f"http://127.0.0.1:{port}"

    print("=" * 50)
    print("  Blackboard Backup - Web App")
    print(f"  Abriendo {url}")
    print("  Presiona Ctrl+C para detener")
    print("=" * 50)
    print()

    if not os.environ.get("BB_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    from app import app
    from waitress import serve

    serve(app, host="127.0.0.1", port=port, threads=8)
