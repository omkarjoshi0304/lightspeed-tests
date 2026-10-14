"""Simulate the Responses API messages sent by the OpenStack Goose image.

The image uses structured input, streaming, and X-LCS-Merge-Server-Tools.
Goose defaults to store=false and replays history on subsequent requests.
"""

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
import requests

GOOSE_HEADERS = {"X-LCS-Merge-Server-Tools": "true"}
QUESTION = "What is OpenStack?"
CLIENT_TOOL = {
    "type": "function",
    "name": "goose_test_calculate_sum",
    "description": "Add two numbers. Only use for arithmetic questions.",
    "parameters": {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
    "strict": False,
}


@pytest.fixture
def goose_payload(config):
    """Use the same explicit model as the Goose container's provider."""
    model = config["goose"]["model"]
    if not model:
        pytest.fail(
            "Set RHOS_LIGHTSPEED_MODEL to the Goose container's LIGHTSPEED_MODEL "
            "value before running the Goose integration tests."
        )
    return {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": QUESTION}],
            }
        ],
        "store": False,
        "stream": True,
    }


def _sse_events(response: requests.Response) -> Iterator[dict]:
    """Decode SSE data fields, including multiline events and a final partial frame."""
    response.encoding = "utf-8"
    data_lines = []
    for line in response.iter_lines(decode_unicode=True):
        if line == "":
            if data_lines:
                data = "\n".join(data_lines)
                data_lines = []
                if data == "[DONE]":
                    return
                yield json.loads(data)
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))

    if data_lines:
        data = "\n".join(data_lines)
        if data != "[DONE]":
            yield json.loads(data)


def _assistant_text(data: dict) -> str:
    """Read the structured assistant output Goose consumes, excluding tool items."""
    return "".join(
        part["text"]
        for item in data["output"]
        if item["type"] == "message" and item["role"] == "assistant"
        for part in item["content"]
        if part["type"] == "output_text"
    ).strip()


def _completed_response(response: requests.Response) -> dict:
    """Require a usable text stream and the response fields consumed by Goose."""
    assert response.status_code == 200, (
        f"Goose request returned HTTP {response.status_code}"
    )
    media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
    assert media_type.lower() == "text/event-stream"

    created_id = None
    completed = None
    deltas = []
    for event in _sse_events(response):
        event_type = event["type"]
        assert event_type not in {"error", "response.failed", "response.incomplete"}, (
            f"Goose response stream reported {event_type}"
        )
        if event_type == "response.created":
            created_id = event["response"]["id"]
        elif event_type == "response.output_text.delta":
            deltas.append(event["delta"])
        elif event_type == "response.completed":
            assert completed is None, "Stream completed more than once"
            completed = event["response"]

        if event_type in {"response.output_item.added", "response.output_item.done"}:
            assert event["item"]["type"] in {"message", "function_call", "reasoning"}, (
                "Server tool output leaked into the Goose stream"
            )

    assert created_id, "Missing response.created event"
    assert completed is not None, "Stream ended without response.completed"
    assert completed["id"] == created_id
    assert completed["object"] == "response"
    assert completed["status"] == "completed"
    assert isinstance(completed["created_at"], int)
    assert isinstance(completed["model"], str) and completed["model"]
    assert all(
        item["type"] in {"message", "function_call", "reasoning"}
        for item in completed["output"]
    ), "Completed response contains output types Goose cannot consume"
    text = _assistant_text(completed)
    assert text, "No assistant output_text in completed response"
    assert deltas and "".join(deltas).strip() == text, (
        "Streamed text does not match the completed assistant answer"
    )
    return completed


def _ask_goose(client, payload: dict) -> dict:
    """Close the HTTP response even when a stream assertion fails."""
    with client.responses(payload, headers=GOOSE_HEADERS) as response:
        return _completed_response(response)


def build_goose_history(first_input: list[dict], first_response: dict) -> list[dict]:
    """Copy the input history and append nonempty assistant text messages."""
    history = list(first_input)

    for item in first_response.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            usable_parts = []

            for part in item.get("content", []):
                if part.get("type") == "output_text" and part.get("text"):
                    usable_parts.append(
                        {
                            "type": part["type"],
                            "text": part["text"],
                            "annotations": part.get("annotations", []),
                        }
                    )

            if usable_parts:
                history.append(
                    {"type": "message", "role": "assistant", "content": usable_parts}
                )
    return history


class TestGooseResponses:
    def test_goose_streaming_question_returns_answer(self, client, goose_payload):
        """Goose's structured question must produce a complete OpenStack answer."""
        data = _ask_goose(client, goose_payload)

        assert "openstack" in _assistant_text(data).lower()

    def test_goose_follow_up_accepts_message_history(self, client, goose_payload):
        """Recall a unique OpenStack project name from replayed message history."""
        project_name = f"goose-history-{uuid4().hex[:12]}"
        first_payload = {
            **goose_payload,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"My OpenStack project is named {project_name}. "
                                f"{QUESTION}"
                            ),
                        }
                    ],
                }
            ],
        }
        first_response = _ask_goose(client, first_payload)
        history = build_goose_history(first_payload["input"], first_response)
        history.append(
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "What is the exact OpenStack project name I gave you "
                            "earlier? Return only the project name."
                        ),
                    }
                ],
            }
        )
        follow_up = _ask_goose(client, {**goose_payload, "input": history})

        assert project_name in _assistant_text(follow_up).lower(), (
            "Follow-up did not recall the OpenStack project name from history"
        )

    def test_goose_question_accepts_client_tools(self, client, goose_payload):
        """Answer an OpenStack question while retaining a Goose client function tool."""
        data = _ask_goose(client, {**goose_payload, "tools": [CLIENT_TOOL]})

        assert any(
            tool.get("type") == "function" and tool.get("name") == CLIENT_TOOL["name"]
            for tool in data.get("tools") or []
        ), "Goose client tool was lost"
        assert "openstack" in _assistant_text(data).lower()
