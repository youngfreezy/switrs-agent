"""Request identity supplied by the host application, never by an MCP tool."""

import json
import pathlib
import secrets


def new_request(question: str) -> dict:
    return {"id": secrets.token_hex(16), "question": question}


def write_request(path: pathlib.Path, question: str) -> None:
    # Atomic replacement: a long-lived MCP subprocess sees either complete request.
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(new_request(question)))
    pending.replace(path)


def read_request(path: pathlib.Path) -> dict:
    try:
        request = json.loads(path.read_text())
        if (isinstance(request, dict) and isinstance(request.get("id"), str) and request["id"]
                and isinstance(request.get("question"), str) and request["question"].strip()):
            return request
    except (OSError, ValueError):
        pass
    raise ValueError("The application request binding is missing or invalid; start a new question.")


def same_question(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def matches_question(result: dict, question: str) -> bool:
    return isinstance(result.get("question"), str) and same_question(result["question"], question)
