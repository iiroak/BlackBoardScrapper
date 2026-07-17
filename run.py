#!/usr/bin/env python3
"""Ejecuta la aplicación web de Blackboard Backup.

Uso:
    python run.py

Abre automáticamente el navegador en http://localhost:5000
"""

import os
import threading
import webbrowser

if __name__ == "__main__":
    port = 5000
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
    app.run(debug=True, port=port, threaded=True)
