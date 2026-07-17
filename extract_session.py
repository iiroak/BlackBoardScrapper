"""Script CLI para extraer sesión de Blackboard.

Ejecutar:    python extract_session.py

El script abre Chromium automáticamente.
Inicia sesión con tu cuenta institucional.
Cuando las cookies se detecten, se cerrará solo.
Las cookies se guardan en session.json.

Luego ejecuta: python run.py
"""

from auth import extract_cookies_playwright, save_session, validate_session, make_session


def main():
    print("=" * 60)
    print("  BLACKBOARD BACKUP - EXTRACT SESSION")
    print("=" * 60)
    print()
    print("  Se abrirá una ventana de Chromium")
    print("  Inicia sesión con tu cuenta institucional (SAML)")
    print("  Cuando las cookies se detecten, se cerrará solo")
    print()

    try:
        cookies, xsrf = extract_cookies_playwright(wait_for_login=True)
    except Exception as e:
        print(f"\n  Error: {e}")
        return

    if not cookies or not any(c.get("name") == "BbRouter" for c in cookies):
        print("  No se detectó la sesión. Asegúrate de haber iniciado sesión correctamente.")
        print("  Si el problema persiste, usa: python run.py y elige 'Pegar cookies'")
        return

    session = make_session(cookies, xsrf)
    user = validate_session(session)
    if user:
        name = f"{user.get('givenName', '')} {user.get('familyName', '')}".strip()
        save_session(cookies, xsrf)
        print(f"  Sesión guardada: {name}")
        print(f"\n  Ahora ejecuta: python run.py")
    else:
        print("  Error: la sesión no es válida. Intenta de nuevo.")


if __name__ == "__main__":
    main()
