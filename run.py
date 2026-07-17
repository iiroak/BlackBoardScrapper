#!/usr/bin/env python3
"""Ejecuta la aplicación web de Blackboard Backup.

Uso:
    python run.py

Abre automáticamente el navegador en http://localhost:5000
Muestra un icono en la bandeja del sistema para cerrar la aplicación.
"""

import os
import socket
import sys
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


def _resource_path(relative):
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(__file__), relative)


def _start_server(port):
    from app import app
    from waitress import serve

    serve(app, host="127.0.0.1", port=port, threads=8)


def _create_tray(url, shutdown_event):
    try:
        import pystray
        from PIL import Image

        icon_path = _resource_path("icon.png")
        image = Image.open(icon_path)
    except Exception:
        return

    def open_browser(icon=None, item=None):
        webbrowser.open(url)

    def quit_app(icon=None, item=None):
        icon.stop()
        shutdown_event.set()

    menu = pystray.Menu(
        pystray.MenuItem("Abrir Campus Archive", open_browser, default=True),
        pystray.MenuItem("Cerrar", quit_app),
    )

    tray = pystray.Icon(
        "Campus Archive",
        image,
        "Campus Archive",
        menu,
    )

    tray.run()


if __name__ == "__main__":
    port = int(os.environ.get("BB_PORT", _available_port()))
    url = f"http://127.0.0.1:{port}"
    shutdown_event = threading.Event()

    print("=" * 50)
    print("  Campus Archive")
    print(f"  Abriendo {url}")
    print("  Cerrá desde la bandeja del sistema")
    print("=" * 50)
    print()

    server_thread = threading.Thread(target=_start_server, args=(port,), daemon=True)
    server_thread.start()

    if not os.environ.get("BB_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    if os.environ.get("BB_NO_TRAY"):
        try:
            shutdown_event.wait()
        except KeyboardInterrupt:
            shutdown_event.set()
    else:
        _create_tray(url, shutdown_event)
