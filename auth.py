import json
import re
from pathlib import Path

import requests

from config import BASE_URL, USER_DATA_DIR, USER_AGENT

SESSION_FILE = USER_DATA_DIR / "session.json"
LEGACY_SESSION_FILE = Path("./session.json")


def _extract_xsrf(bbrouter_value: str) -> str | None:
    m = re.search(r'xsrf:([0-9a-f-]+)', bbrouter_value)
    return m.group(1) if m else None


def _get_xsrf_from_cookies(cookies_list: list[dict]) -> str | None:
    for c in cookies_list:
        if c.get("name") == "BbRouter":
            return _extract_xsrf(c.get("value", ""))
    return None


def normalize_cookie_header(raw: str) -> str:
    """Accept a Cookie header or copied DevTools request-header JSON."""
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if value.lower().startswith("curl "):
        match = re.search(r"(?:-H|--header)(?:=|\s+)(?:\^?(['\"]))Cookie:\s*(.*?)\^?\1", value, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(2).strip()
        match = re.search(r"(?:-b|--cookie)(?:=|\s+)(?:\^?(['\"]))(.*?)\^?\1", value, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(2).strip()
    if value.lower().startswith("cookie:"):
        return value.split(":", 1)[1].strip()
    if not value.startswith("{"):
        return value
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return value
    container = payload.get("requestHeaders", payload) if isinstance(payload, dict) else {}
    headers = container.get("headers", []) if isinstance(container, dict) else []
    for header in headers:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == "cookie":
            cookie_value = header.get("value", "")
            return cookie_value.strip() if isinstance(cookie_value, str) else ""
    return value


def make_cookie_jar(cookies_list: list[dict]) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    for c in cookies_list:
        jar.set(
            c.get("name", ""),
            c.get("value", ""),
            domain=c.get("domain", "campusvirtual.umayor.cl"),
            path=c.get("path", "/"),
        )
    return jar


def make_session(cookies_list: list[dict], xsrf: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9",
    })
    session.cookies = make_cookie_jar(cookies_list)
    if xsrf:
        session.headers["X-Blackboard-XSRF"] = xsrf
    return session


def validate_session(session: requests.Session) -> dict | None:
    try:
        resp = session.get(
            f"{BASE_URL}/learn/api/v1/users/me",
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def save_session(cookies, xsrf):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cookies": [
            {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
            for c in cookies
        ],
        "xsrf": xsrf,
    }
    SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_session() -> tuple[requests.Session | None, str | None]:
    for session_file in (SESSION_FILE, LEGACY_SESSION_FILE):
        if not session_file.exists():
            continue
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            cookies = data.get("cookies", [])
            xsrf = data.get("xsrf")
            if not cookies:
                continue
            session = make_session(cookies, xsrf)
            user = validate_session(session)
            if user:
                if session_file != SESSION_FILE:
                    save_session(cookies, xsrf)
                return session, xsrf
        except Exception:
            continue
    return None, None


def login_flow() -> tuple[requests.Session | None, str | None]:
    print("=" * 60)
    print("  BLACKBOARD BACKUP SCRAPER - AUTENTICACIÓN")
    print("=" * 60)
    print()

    session, xsrf = load_session()
    if session:
        user = validate_session(session)
        if user:
            name = f"{user.get('givenName', '')} {user.get('familyName', '')}".strip()
            print(f"  ✓ Sesión cargada desde session.json")
            print(f"  Usuario: {name}")
            return session, xsrf

    print("  No hay una sesión guardada.")
    print("  Abre la interfaz web, conecta las cookies de Blackboard y vuelve a ejecutar el CLI.")
    return None, None


def get_console_script() -> str:
    return """// ─── Blackboard Backup - Verificar sesión ───
// 1. Abre https://campusvirtual.umayor.cl y loguéate
// 2. F12 → Console, pega esto y ENTER
// 3. Si el navegador permite leer las cookies, las copiará automáticamente

(async () => {
  const url = window.location.origin + '/learn/api/v1/users/me';
  try {
    const r = await fetch(url, { credentials: 'include' });
    if (!r.ok) { console.log('✗ No estás logueado (HTTP ' + r.status + ')'); return; }
    const user = await r.json();
    console.log('✓ Sesión activa:', user.givenName, user.familyName);
    const cookies = document.cookie;
    if (cookies.includes('BbRouter=')) {
      try {
        copy(cookies);
        console.log('✓ Cookies copiadas al portapapeles. Pégalas en la app web.');
      } catch (e) {
        console.log('✓ Cookies disponibles. Cópialas manualmente:');
        console.log(cookies);
      }
    } else {
      console.log('⚠ La sesión está activa, pero las cookies están protegidas como HttpOnly.');
      console.log('  F12 → Network → clic en una request → Request Headers → copia Cookie');
    }
  } catch (e) {
    console.log('✗ Error:', e.message);
  }
})();
"""


def console_session_setup(user_data: dict, cookies_str: str) -> tuple[requests.Session, str | None]:
    cookies_str = normalize_cookie_header(cookies_str)
    cookies = []
    for part in cookies_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies.append({"name": k, "value": v, "domain": "campusvirtual.umayor.cl", "path": "/"})
    xsrf = _get_xsrf_from_cookies(cookies)
    session = make_session(cookies, xsrf)
    validated = validate_session(session)
    if validated:
        save_session(cookies, xsrf)
        return session, xsrf
    return None, None
