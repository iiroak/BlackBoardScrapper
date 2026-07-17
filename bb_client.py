import time
from typing import Any

import requests

from config import (
    API_BASE,
    PUBLIC_API_BASE,
    BASE_URL,
    MAX_RETRIES,
    RETRY_DELAY,
    REQUEST_TIMEOUT,
    PAGE_SIZE,
)


class BBClient:
    def __init__(self, session: requests.Session, xsrf: str | None):
        self.session = session
        self.xsrf = xsrf

    def request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_body: Any = None,
        headers: dict | None = None,
        stream: bool = False,
        timeout: int | None = None,
    ) -> requests.Response:
        hdrs = {"Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        if self.xsrf:
            hdrs.setdefault("X-Blackboard-XSRF", self.xsrf)

        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_body,
                    headers=hdrs,
                    stream=stream,
                    timeout=timeout or REQUEST_TIMEOUT,
                )
                if resp.status_code in (401, 403):
                    print(f"  ⚠ Error {resp.status_code}: posible sesión expirada")
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"Server error {resp.status_code}")
                return resp
            except (requests.ConnectionError, requests.HTTPError, requests.Timeout) as e:
                last_exc = e
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_DELAY * (attempt + 1)
                    print(f"  ⚠ Reintentando ({attempt+1}/{MAX_RETRIES}) en {wait}s...")
                    time.sleep(wait)
        raise last_exc

    def get_json(
        self, url: str, params: dict | None = None, timeout: int | None = None
    ) -> dict | list:
        resp = self.request("GET", url, params=params, timeout=timeout)
        if resp.status_code == 204:
            return {}
        return resp.json()

    def get_text(self, url: str, params: dict | None = None) -> str:
        resp = self.request("GET", url, params=params)
        return resp.text

    def paginate(
        self, url: str, params: dict | None = None, limit: int = PAGE_SIZE
    ) -> list[dict]:
        results = []
        offset = 0
        query_params = dict(params or {})
        query_params["limit"] = limit

        while True:
            query_params["offset"] = offset
            data = self.get_json(url, params=query_params)
            items = data.get("results", [])
            results.extend(items)
            paging = data.get("paging", {}) or {}
            next_page = paging.get("nextPage", "")
            if not next_page:
                break
            offset += limit

        return results

    def get_user(self) -> dict:
        return self.get_json(f"{API_BASE}/users/me", params={"expand": "systemRoles,insRoles"})

    def get_courses(self, user_id: str) -> list[dict]:
        params = {
            "expand": "course.effectiveAvailability,course.permissions,courseRole",
            "includeCount": "true",
        }
        return self.paginate(f"{API_BASE}/users/{user_id}/memberships", params, limit=1000)

    def get_course(self, course_id: str) -> dict:
        params = {"expand": "instructorsMembership,term"}
        return self.get_json(f"{API_BASE}/courses/{course_id}", params=params)

    def get_content_children(
        self, course_id: str, content_id: str = "ROOT", original_offset: int = 0
    ) -> list[dict]:
        results = []
        limit = 10
        offset = original_offset

        while True:
            params = {
                "offset": offset,
                "limit": limit,
                "@view": "Summary",
            }
            data = self.get_json(
                f"{API_BASE}/courses/{course_id}/contents/{content_id}/children",
                params=params,
            )
            items = data.get("results", [])
            results.extend(items)
            paging = data.get("paging", {}) or {}
            next_page = paging.get("nextPage", "")
            if not next_page:
                break
            offset += limit

        return results

    def get_course_announcements(self, course_id: str) -> list[dict]:
        params = {"sort": "startDateRestriction(desc)"}
        return self.paginate(f"{API_BASE}/courses/{course_id}/announcements", params, limit=10)

    def get_conversations(self, course_id: str) -> list[dict]:
        return self.paginate(
            f"{API_BASE}/courses/{course_id}/conversations",
            {"fields": ",-participantIds"},
            limit=25,
        )

    def get_conversation_messages(self, course_id: str, conv_id: str) -> list[dict]:
        return self.get_json(
            f"{API_BASE}/courses/{course_id}/conversations/{conv_id}/messages",
            params={"sort": "postDate(asc)"},
        ).get("results", [])

    def get_conversation_participants(self, course_id: str, conv_id: str) -> list[dict]:
        return self.get_json(
            f"{API_BASE}/courses/{course_id}/conversations/{conv_id}/participants",
            params={"limit": 3},
        ).get("results", [])

    def get_gradebook_grades(self, course_id: str, user_id: str) -> list[dict]:
        params = {
            "userId": user_id,
            "sort": "hasBeenViewedByStudent(asc),column.position(asc)",
            "expand": "lastAttempt,attemptsLeft,submissionStatus",
            "includeNoGradeItems": "true",
            "skipExternalGrade": "true",
            "skipKnowledgeCheck": "true",
        }
        return self.paginate(
            f"{API_BASE}/courses/{course_id}/gradebook/grades", params, limit=25
        )

    def get_final_grade(self, course_id: str) -> dict:
        params = {"expand": "displayGrade"}
        return self.get_json(
            f"{API_BASE}/courses/{course_id}/gradebook/columns/finalGrade", params=params
        )

    def get_course_tools(self, course_id: str) -> list[dict]:
        return self.paginate(
            f"{API_BASE}/courses/{course_id}/tools", params={"limit": 100}, limit=100
        )

    def get_collab_sessions(self, course_id: str) -> list[dict]:
        return self.get_json(
            f"{API_BASE}/courses/{course_id}/collabultra/sessions"
        ).get("results", [])
