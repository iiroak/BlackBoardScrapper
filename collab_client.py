import re
import time
from urllib.parse import urlparse, parse_qs

import requests

from config import COLLAB_BASE, BASE_URL, USER_AGENT


class CollabClient:
    def __init__(self, bb_session: requests.Session, xsrf: str | None):
        self.bb_session = bb_session
        self.xsrf = xsrf
        self.jwt: str | None = None
        self.collab_session: requests.Session | None = None

    def _try_get_jwt_from_bb(self, course_id: str) -> str | None:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Blackboard-XSRF": self.xsrf or "",
        }
        url = f"{BASE_URL}/learn/api/v1/courses/{course_id}/collabultra/sessions"
        resp = self.bb_session.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        sessions = data.get("results", []) if isinstance(data, dict) else data
        for s in (sessions if isinstance(sessions, list) else []):
            if isinstance(s, dict):
                token = s.get("token") or s.get("jwt") or s.get("launchToken")
                if token:
                    return token
        return None

    def _extract_jwt_from_html(self, html: str) -> str | None:
        m = re.search(r'token=([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)', html)
        if m:
            return m.group(1)
        m = re.search(r'"token"\s*:\s*"([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)"', html)
        if m:
            return m.group(1)
        m = re.search(r'(eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)', html)
        if m:
            return m.group(1)
        return None

    def _try_lti_launch(self, course_id: str) -> str | None:
        launch_url = f"{BASE_URL}/learn/api/v1/lti/launch"
        params = {
            "course_id": course_id,
            "placement": "collabultra",
            "type": "recording",
        }
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "X-Blackboard-XSRF": self.xsrf or "",
        }
        resp = self.bb_session.get(launch_url, params=params, headers=headers, timeout=30)
        if resp.status_code == 200:
            jwt = self._extract_jwt_from_html(resp.text)
            if jwt:
                return jwt
        return None

    def _try_direct_collab_launch(self, course_id: str) -> str | None:
        url = f"{BASE_URL}/ultra/courses/{course_id}/outline/collab/launchRecordings"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "X-Blackboard-XSRF": self.xsrf or "",
        }
        resp = self.bb_session.get(url, headers=headers, timeout=30, allow_redirects=True)
        if resp.status_code == 200:
            jwt = self._extract_jwt_from_html(resp.text)
            if jwt:
                return jwt
        for r in resp.history:
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                m = re.search(r'token=([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)', loc)
                if m:
                    return m.group(1)
        return None

    def _init_collab_session(self, jwt: str):
        self.jwt = jwt
        self.collab_session = requests.Session()
        self.collab_session.headers.update({
            "Authorization": f"Bearer {jwt}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        })

    def authenticate(self, course_id: str) -> bool:
        jwt = self._try_get_jwt_from_bb(course_id)
        if jwt:
            self._init_collab_session(jwt)
            return True

        jwt = self._try_lti_launch(course_id)
        if jwt:
            self._init_collab_session(jwt)
            return True

        jwt = self._try_direct_collab_launch(course_id)
        if jwt:
            self._init_collab_session(jwt)
            return True

        return False

    def get_recordings(self, course_id: str) -> list[dict]:
        if not self.collab_session:
            return []
        try:
            resp = self.collab_session.get(
                f"{COLLAB_BASE}/recordings",
                params={
                    "startTime": "2020-01-01T00:00:00-0400",
                    "endTime": "2030-12-31T23:59:59-0400",
                    "sort": "endTime",
                    "order": "desc",
                    "limit": 100,
                    "offset": 0,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
        except Exception:
            pass
        return []

    def get_recording_url(self, recording_id: str) -> str | None:
        if not self.collab_session:
            return None
        try:
            resp = self.collab_session.get(
                f"{COLLAB_BASE}/recordings/{recording_id}/url",
                params={"validHours": 0, "validMinutes": 10},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("url")
        except Exception:
            pass
        return None

    def download_recording(self, recording_id: str, recording_name: str, dest_path, progress_callback=None) -> bool:
        if not self.collab_session:
            return False
        try:
            launch_url = self.get_recording_url(recording_id)
            if not launch_url:
                if progress_callback:
                    progress_callback(f"No se pudo obtener URL para {recording_name}")
                return False

            resp = self.collab_session.get(launch_url, stream=True, timeout=300, allow_redirects=True)
            if resp.status_code != 200:
                if progress_callback:
                    progress_callback(f"Error HTTP {resp.status_code} al descargar {recording_name}")
                return False

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total > 0:
                            progress_callback(f"Descargando {recording_name}... {downloaded//1024}/{total//1024} KB")

            if progress_callback:
                progress_callback(f"{recording_name} descargado")
            return True
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error descargando {recording_name}: {e}")
            return False
