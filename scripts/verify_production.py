"""Verify production dependencies and, optionally, a private document RAG flow."""

import argparse
import os
from pathlib import Path
import sys
import time

import requests


def _detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        return "request failed"
    detail = payload.get("detail", "request failed")
    return detail if isinstance(detail, str) else "request failed"


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    timeout: float = 15,
    **kwargs,
) -> requests.Response:
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            timeout=timeout,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Could not connect to the Company AI API.") from exc
    if not response.ok:
        raise RuntimeError(
            f"API request failed with status {response.status_code}: "
            f"{_detail(response)}"
        )
    return response


def verify(
    api_base_url: str,
    *,
    email: str | None = None,
    password: str | None = None,
    upload_file: Path | None = None,
    question: str | None = None,
    timeout_seconds: int = 180,
) -> None:
    base_url = api_base_url.rstrip("/")
    readiness = _request("GET", f"{base_url}/health/ready")
    dependencies = readiness.json().get("dependencies", {})
    print("Dependency readiness:")
    for name, state in dependencies.items():
        print(f"  {name}: {state}")

    if email is None and password is None and upload_file is None:
        print("Read-only verification complete.")
        return
    if not email or not password:
        raise ValueError(
            "Set both COMPANY_AI_TEST_EMAIL and COMPANY_AI_TEST_PASSWORD."
        )

    login_response = _request(
        "POST",
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
    )
    token = login_response.json().get("access_token")
    if not token:
        raise RuntimeError("Login response did not contain an access token.")
    _request(
        "GET",
        f"{base_url}/documents",
        token=token,
    )
    print("Authenticated document listing: ok")

    if upload_file is None:
        return
    if not question or not question.strip():
        raise ValueError("--question is required with --upload.")

    document_id = None
    try:
        with upload_file.open("rb") as document_stream:
            response = _request(
                "POST",
                f"{base_url}/documents/upload",
                token=token,
                files={
                    "file": (
                        upload_file.name,
                        document_stream,
                        "application/octet-stream",
                    )
                },
                data={"access_scope": "private"},
                timeout=60,
            )
        document_id = response.json().get("id")
        if not isinstance(document_id, int):
            raise RuntimeError("Upload response did not contain a document ID.")

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status_response = _request(
                "GET",
                f"{base_url}/documents/{document_id}/status",
                token=token,
            )
            status_payload = status_response.json()
            state = status_payload.get("processing_status")
            if state == "ready":
                print(
                    "Document processing: ready "
                    f"({status_payload.get('chunk_count', 0)} chunks)"
                )
                break
            if state == "failed":
                raise RuntimeError("Document processing failed.")
            time.sleep(2)
        else:
            raise TimeoutError("Document processing did not finish in time.")

        rag_response = _request(
            "POST",
            f"{base_url}/rag/query",
            token=token,
            json={"question": question, "top_k": 5},
            timeout=60,
        )
        sources = rag_response.json().get("sources", [])
        if not any(
            source.get("document_id") == document_id
            for source in sources
            if isinstance(source, dict)
        ):
            raise RuntimeError(
                "RAG completed but did not cite the uploaded test document."
            )
        print("RAG retrieval and citation: ok")
    finally:
        if document_id is not None:
            _request(
                "DELETE",
                f"{base_url}/documents/{document_id}",
                token=token,
            )
            print("Temporary test document deleted.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upload",
        type=Path,
        help="Optionally upload a test document, verify RAG, and delete it.",
    )
    parser.add_argument(
        "--question",
        help="Question answerable from the test document; required with --upload.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Maximum document-processing wait in seconds (default: 180).",
    )
    arguments = parser.parse_args()

    api_base_url = os.environ.get("COMPANY_AI_API_URL")
    if not api_base_url:
        parser.error("Set COMPANY_AI_API_URL in the process environment.")
    email = os.environ.get("COMPANY_AI_TEST_EMAIL")
    password = os.environ.get("COMPANY_AI_TEST_PASSWORD")

    try:
        verify(
            api_base_url,
            email=email,
            password=password,
            upload_file=arguments.upload,
            question=arguments.question,
            timeout_seconds=arguments.timeout,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
