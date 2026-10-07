import json
import uuid
from pathlib import Path

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tools import TOOLS, run_tool

# --- Config ---

SYSTEM_PROMPT = (
    "You are BingeWise, a deadline-aware TV watching planner. "
    "Your job is to help users decide whether they can finish a TV series "
    "before a deadline and create realistic viewing plans using real episode data. "

    "When a user mentions a TV show by name, use search_show to identify the correct series. "
    "Never invent a TMDB show ID. "

    "When the user asks about a show's episode count, total runtime, or how long it takes "
    "to watch, use get_watch_time with the show ID returned by search_show. "

    "When the user asks whether they can finish a show before a deadline or asks for a "
    "watching schedule, use plan_binge_schedule. Convert hours of availability into minutes "
    "before calling the tool. Never calculate the schedule yourself. "

    "For follow-up questions, remember information already provided in the current conversation, "
    "including the TV show, deadline, weekday availability, and weekend availability. "
    "If the user changes only one condition, keep the other previously stated conditions unchanged. "

    "Do not invent dates, episode counts, runtimes, or schedule results. "
    "The start date for plan_binge_schedule is determined automatically by the tool. "

    "If the user has not provided information required to create a schedule, such as a deadline "
    "or viewing availability, ask a concise follow-up question instead of guessing. "

    "When presenting a schedule result, clearly state whether the user can meet the deadline, "
    "the projected finish date, and how many days early or late they will finish. "
    "If they cannot meet the deadline, also mention how many episodes and watch minutes "
    "will remain at the deadline. If recommended_weekday_minutes and "
    "recommended_weekend_minutes are provided by the tool, clearly tell the user that "
    "increasing their daily viewing time to approximately those amounts would allow them "
    "to finish by the deadline. "

    "Keep responses practical, concise, and easy to understand."
)
MAX_TOOL_ROUNDS = 5

# --- The Harness ---


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Complete until the model answers without asking for a tool.

    Returns the final text and a record of every tool call made along the way.
    """
    tool_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model="vertex_ai/gemini-3.5-flash-lite",
            vertex_location="global",
            messages=messages,
            tools=TOOLS,
        ).choices[0].message

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content, tool_calls

        # The harness, not the model, runs each tool and appends the result
        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

    return "Sorry, I hit my tool-call limit before finishing.", tool_calls


# --- Session Store ---

# session_id -> list of messages. In-memory, single process.
sessions: dict[str, list] = {}

# --- FastAPI App ---

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Get or create the session
    session_id = request.session_id or str(uuid.uuid4())
    if session_id not in sessions:
        sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Append user's message to the context
    sessions[session_id] += [{"role": "user", "content": request.message}]

    try:
        response, tool_calls = run_agent(sessions[session_id])
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
