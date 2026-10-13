"""Ask, the research assistant."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api import schemas
from api.security import CurrentUser, current_user
from portfolio_tracker import config, operations
from portfolio_tracker.services import assistant

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

# Each question is a paid API call, so one account cannot fire them without
# limit. Counted in the database (see operations.assistant_wait_seconds), so
# the limit holds across restarts, worker processes and both front ends.
# The same figures as the Flask app's, which share that count.
ASSISTANT_LIMIT = 20
ASSISTANT_WINDOW = 600  # seconds

# "busy" is this app's own per-account limit; "unavailable" is the AI
# provider out of quota or failing.
_STATUS = {"invalid": 400, "busy": 429, "unavailable": 503, "disabled": 503}


@router.get("/status", response_model=schemas.AssistantStatus)
def status(user: CurrentUser = Depends(current_user)):
    return schemas.AssistantStatus(
        enabled=config.ASSISTANT_ENABLED,
        research=assistant.research_available(),
        research_notice=assistant.SEARCH_UNAVAILABLE_NOTICE,
        max_question_chars=assistant.MAX_QUESTION_CHARS,
    )


@router.post("", response_model=schemas.AssistantAnswer)
def ask(body: schemas.AssistantQuestion, user: CurrentUser = Depends(current_user)):
    """Ask a question. A failure carries the same body with ok=false."""
    wait = operations.assistant_wait_seconds(user.id, ASSISTANT_LIMIT, ASSISTANT_WINDOW)
    if wait:
        answer = assistant.Answer(
            False, "", "busy",
            f"You've asked {ASSISTANT_LIMIT} questions in {ASSISTANT_WINDOW // 60} minutes. "
            "Ask again shortly.",
            retry_after=wait,
        )
    else:
        # Built on the server from this app's records. Nothing numeric from
        # the browser reaches the model; the page only says which ticker
        # it shows.
        context = operations.assistant_context(user.id, body.symbol)
        answer = assistant.ask(
            context, body.question,
            [{"question": turn.question, "answer": turn.answer} for turn in body.earlier],
        )

    payload = schemas.AssistantAnswer(
        ok=answer.ok,
        answer=answer.text,
        kind=answer.kind,
        message=answer.message,
        detail=answer.message,
        sources=[schemas.AssistantSource(title=src.title, uri=src.uri, domain=src.domain)
                 for src in answer.sources],
        search_suggestions=answer.suggestions_html,
        notice=answer.notice,
        searches=list(answer.searches),
        retry_after=answer.retry_after,
        research=assistant.research_available(),
    )
    if answer.ok:
        return payload
    return JSONResponse(
        payload.model_dump(),
        status_code=_STATUS.get(answer.kind, 502),
        headers={"Retry-After": str(answer.retry_after)} if answer.retry_after else None,
    )
