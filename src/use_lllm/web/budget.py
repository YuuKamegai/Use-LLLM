"""Persist request budgets across approval resumes and model client reconstruction."""

from .contracts import failure

LIMITS = {"web::search": 4, "web::read_page": 6}


def reset_web_budget(sessions, session_id):
    sessions.update_session(session_id, state_patch={"web_budget": {"search": 0, "read_page": 0}})


def consume_web_budget(sessions, session_id, tool_name):
    if tool_name not in LIMITS:
        return None
    state = sessions.get_session(session_id)["state"]
    counts = dict(state.get("web_budget", {"search": 0, "read_page": 0}))
    key = tool_name.split("::", 1)[1]
    if counts.get(key, 0) >= LIMITS[tool_name]:
        return failure(
            "rate_limited",
            "この発話のWeb実行上限に達しました。取得済み資料で回答してください。",
            limit_scope="turn",
        )
    counts[key] = counts.get(key, 0) + 1
    sessions.update_session(session_id, state_patch={"web_budget": counts})
    return None
