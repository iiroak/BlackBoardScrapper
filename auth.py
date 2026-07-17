import json
import re
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from config import BASE_URL, USER_AGENT

SESSION_FILE = Path("./session.json")


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


def extract_cookies_playwright(wait_for_login: bool = True) -> tuple[list[dict], str | None]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="es-ES",
        )
        page = context.new_page()
        page.goto(f"{BASE_URL}/ultra/institution-page", wait_until="domcontentloaded")

        if wait_for_login:
            start = time.time()
            while time.time() - start < 300:
                time.sleep(1.5)
                cookies = context.cookies()
                if any(c.get("name") == "BbRouter" for c in cookies):
                    time.sleep(1)
                    cookies = context.cookies()
                    break
            else:
                cookies = context.cookies()

        cookies = context.cookies()
        xsrf = _get_xsrf_from_cookies(cookies)
        browser.close()
        return cookies, xsrf


def save_session(cookies, xsrf):
    data = {
        "cookies": [
            {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
            for c in cookies
        ],
        "xsrf": xsrf,
    }
    SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_session() -> tuple[requests.Session | None, str | None]:
    if not SESSION_FILE.exists():
        return None, None
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        xsrf = data.get("xsrf")
        if not cookies:
            return None, None
        session = make_session(cookies, xsrf)
        user = validate_session(session)
        if user:
            return session, xsrf
    except Exception:
        pass
    return None, None


def login_flow() -> tuple[requests.Session, str | None]:
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

    print("  Abriendo Chromium para login...")
    print("  → Se abrirá una ventana de Chromium")
    print("  → Inicia sesión con tu cuenta institucional")
    print("  → Cuando las cookies se detecten, se cerrará solo")
    print()

    try:
        cookies, xsrf = extract_cookies_playwright(wait_for_login=True)
    except Exception as e:
        print(f"\n  ✗ Error con Playwright: {e}")
        raw = input("  Pega las cookies manualmente (o ENTER para salir): ").strip()
        if not raw:
            return None, None
        cookies = []
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                cookies.append({"name": k, "value": v, "domain": "campusvirtual.umayor.cl", "path": "/"})
        xsrf = _get_xsrf_from_cookies(cookies)

    if not cookies or not any(c.get("name") == "BbRouter" for c in cookies):
        print("  ✗ No se detectó sesión activa (cookie BbRouter no encontrada).")
        return None, None

    session = make_session(cookies, xsrf)
    user = validate_session(session)
    if user:
        name = f"{user.get('givenName', '')} {user.get('familyName', '')}".strip()
        print(f"\n  ✓ Sesión válida: {name}")
        save_session(cookies, xsrf)
        return session, xsrf
    else:
        print("\n  ✗ Sesión inválida.")
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
