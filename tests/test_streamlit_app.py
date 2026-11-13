from pathlib import Path
from types import SimpleNamespace

import requests
from streamlit.testing.v1 import AppTest

from app.clients import document_api_client


def test_chat_sends_only_current_message_not_prior_answers(monkeypatch):
    captured = []

    def fake_post(_url, *, json, **_kwargs):
        captured.append(json)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"answer": "Company AI reply.", "sources": []},
        )

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200),
    )
    monkeypatch.setattr(
        document_api_client,
        "list_documents",
        lambda *_args, **_kwargs: [],
    )
    app_path = Path(__file__).parents[1] / "streamlit_app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10)
    app.session_state["token"] = "synthetic-token"
    app.session_state["user_info"] = {"email": "employee@example.test"}
    app.session_state["messages"] = [
        {"role": "user", "content": "Previous question"},
        {
            "role": "assistant",
            "content": "PRIVATE_PREVIOUS_ANSWER_SENTINEL",
            "sources": [],
        },
    ]

    app.run()
    app.chat_input[0].set_value("how can u help me").run()

    assert not app.exception
    assert captured == [{"message": "how can u help me"}]
