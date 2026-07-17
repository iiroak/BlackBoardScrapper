# Campus Archive — Blackboard Backup Scraper

Herramienta web para respaldar todo el contenido de Blackboard Learn (Universidad Mayor) y almacenarlo localmente o en OneDrive.

![Campus Archive Dashboard](images/image.png)

## Funcionalidades

- **Descarga completa de cursos** — archivos, carpetas, metadatos, anuncios, mensajes, notas y grabaciones Collaborate
- **Panel web** — interfaz Flask con diseño responsive, modo claro
- **Almacenamiento flexible** — carpeta local o OneDrive (detección automática en Windows/WSL)
- **Sincronización incremental** — solo descarga archivos nuevos o modificados (SHA-256)
- **Verificación de integridad** — escaneo de archivos locales vs remotos con barra de progreso
- **Manifiesto local** — `manifest.json` con hash, tamaño, fecha de modificación por archivo
- **Organización automática** — `Semestre/Curso/contenido/` con metadatos JSON
- **Soporte Collaborate** — detección y descarga de grabaciones de sesiones
- **Autenticación local** — valida cookies de una sesión activa sin guardar contraseñas
- **Actualización automática** — verifica nuevas versiones al iniciar y notifica en la bandeja

## Requisitos

- Python 3.10+ (solo para ejecutar desde el código fuente)
- OneDrive (opcional, para almacenamiento en nube)

## Instalación

### Windows (recomendado para usuarios finales)

Descarga `Campus-Archive-Setup.exe` desde la sección [Releases](https://github.com/iiroak/BlackBoardScrapper/releases), ejecútalo y sigue el asistente. No necesitas Python, Git, WSL ni instalar dependencias.

El instalador crea accesos en Inicio y escritorio, abre la aplicación en el navegador predeterminado y agrega un desinstalador en Configuración de Windows. El backup se conserva al desinstalar.

También puedes descargarlo desde PowerShell:

```powershell
irm https://raw.githubusercontent.com/iiroak/BlackBoardScrapper/main/install.ps1 | iex
```

### Linux

Descarga `Campus-Archive-x86_64.AppImage` desde [Releases](https://github.com/iiroak/BlackBoardScrapper/releases), dale permiso de ejecución y ábrelo.

Para instalarlo automáticamente en el menú de aplicaciones:

```bash
bash <(curl -sSL https://raw.githubusercontent.com/iiroak/BlackBoardScrapper/main/install.sh)
```

Después puedes abrirlo con `blackboardscrapper`. Para desinstalarlo usa `uninstall-blackboardscrapper`; tus backups no se borran.

### Manual

```bash
git clone https://github.com/iiroak/BlackBoardScrapper.git
cd BlackBoardScrapper
python -m venv venv
source venv/bin/activate  # o venv\Scripts\activate en Windows
pip install -r requirements.txt
```

## Uso

### Interfaz web

```bash
python run.py
```

Abre `http://localhost:5000`. La app guía el proceso de conexión a Blackboard, escaneo de cursos y descarga.

### Línea de comandos

```bash
python main.py
```

Descarga todos los cursos del usuario autenticado a la carpeta de almacenamiento configurada.

## Estructura del proyecto

```
BlackBoardScrapper/
├── app.py              # Servidor Flask y endpoints API
├── auth.py             # Validación y persistencia de cookies
├── bb_client.py        # Cliente de Blackboard Learn API
├── collab_client.py    # Cliente de Blackboard Collaborate
├── config.py           # Configuración central (URLs, timeouts)
├── content_sync.py     # Sincronización de árbol de contenido
├── downloader.py       # Descarga de archivos con reintentos
├── main.py             # Entry point CLI
├── maintenance.py      # Tareas de mantenimiento
├── manifest.py         # Gestión del manifiesto JSON
├── organizer.py        # Organización de archivos en disco
├── storage.py          # Detección y migración de almacenamiento
├── run.py              # Lanzador de la interfaz web
├── packaging/          # PyInstaller, Inno Setup y AppImage
├── static/             # CSS, JS, SVG
├── templates/          # HTML (Jinja2)
├── tests/              # Tests unitarios
├── images/             # Capturas para documentación
├── requirements.txt
└── LICENSE
```

## Stack

| Componente | Tecnología |
|-----------|-----------|
| Backend | Python 3.12, Flask, Waitress |
| Auth | Cookies de sesión + Blackboard REST API |
| API | Blackboard Learn REST API |
| Almacenamiento | Sistema de archivos local / OneDrive |
| Frontend | HTML5, CSS3, Vanilla JS |

## Logs y diagnóstico

Los logs se guardan en:
- **Windows**: `%LOCALAPPDATA%\Campus Archive\logs\campus-archive.log`
- **Linux**: `~/.local/share/Campus Archive/logs/campus-archive.log`

## Licencia

MIT — ver [LICENSE](LICENSE)
