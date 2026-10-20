"""Publication policy shared by local and hosted transports."""

from .request_binding import matches_question


def latest_certified_result(events: list[dict], question: str) -> dict | None:
    """Validate the latest submission for live output and restored history alike.

    A new submission revokes its predecessor immediately, even if its result was
    lost when the process stopped. Results from earlier calls cannot restore it.
    """
    if not isinstance(question, str) or not question.strip():
        return None
    latest_id = None
    final = None
    for event in events:
        if event.get("kind") == "tool_call" and event.get("tool") == "submit_answer":
            latest_id = event.get("id") if isinstance(event.get("id"), str) else None
            final = None
        elif (event.get("kind") == "tool_result" and latest_id is not None
              and event.get("id") == latest_id):
            result = event.get("result")
            final = (result
                     if not event.get("is_error") and isinstance(result, dict)
                     and result.get("ok") is True and result.get("certified") is True
                     and isinstance(result.get("final_answer"), str) and result["final_answer"].strip()
                     and matches_question(result, question) else None)
    return final


def latest_certificate(events: list[dict], question: str) -> str | None:
    result = latest_certified_result(events, question)
    return result['final_answer'] if result else None

