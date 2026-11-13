"""Authenticated Streamlit client for the document API."""

import requests


class DocumentAPIError(RuntimeError):
    def __init__(self, status_code: int | None, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


def upload_document(
    api_base_url: str,
    token: str,
    *,
    filename: str,
    content: bytes,
    mime_type: str,
    access_scope: str,
) -> dict:
    return _request(
        "POST",
        f"{api_base_url.rstrip('/')}/documents/upload",
        token,
        files={"file": (filename, content, mime_type)},
        data={"access_scope": access_scope},
        timeout=30,
    )


def list_documents(
    api_base_url: str,
    token: str,
    *,
    limit: int = 20,
) -> list[dict]:
    result = _request(
        "GET",
        f"{api_base_url.rstrip('/')}/documents",
        token,
        params={"limit": limit},
        timeout=15,
    )
    if not isinstance(result, list):
        raise DocumentAPIError(None, "The document API returned an invalid list.")
    return result


def get_document_status(
    api_base_url: str,
    token: str,
    document_id: int,
) -> dict:
    result = _request(
        "GET",
        f"{api_base_url.rstrip('/')}/documents/{document_id}/status",
        token,
        timeout=15,
    )
    if not isinstance(result, dict):
        raise DocumentAPIError(
            None,
            "The document API returned an invalid status response.",
        )
    return result


def _request(method: str, url: str, token: str, **kwargs) -> dict | list:
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise DocumentAPIError(
            None,
            "Could not connect to the Company AI API.",
        ) from exc

    if not response.ok:
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            payload = {}
        detail = payload.get("detail", "The document request failed.")
        if not isinstance(detail, str):
            detail = "The document request failed."
        raise DocumentAPIError(response.status_code, detail)

    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise DocumentAPIError(
            response.status_code,
            "The document API returned invalid JSON.",
        ) from exc
    if not isinstance(payload, (dict, list)):
        raise DocumentAPIError(
            response.status_code,
            "The document API returned an invalid response.",
        )
    return payload
