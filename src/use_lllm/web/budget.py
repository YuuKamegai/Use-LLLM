"""Persist request budgets across approval resumes and model client reconstruction."""

from .contracts import failure

LIMITS = {"web::search": 4, "web::read_page": 6}


def reset_web_budget(sessions, session_id):
    sessions.update_session(session_id, state_patch={"web_budget": {"search": 0, "read_page": 0}})
    sessions.update_session(
        session_id, state_patch={"web_evidence": {"attempted": 0, "succeeded": 0}}
    )


def record_web_result(sessions, session_id, result):
    counts = dict(
        sessions.get_session(session_id)["state"].get(
            "web_evidence", {"attempted": 0, "succeeded": 0}
        )
    )
    counts["attempted"] += 1
    if not result.is_error and (result.structured_content or {}).get("status") == "ok":
        counts["succeeded"] += 1
    sessions.update_session(session_id, state_patch={"web_evidence": counts})


def all_web_failed(sessions, session_id):
    evidence = sessions.get_session(session_id)["state"].get("web_evidence", {})
    return evidence.get("attempted", 0) > 0 and evidence.get("succeeded", 0) == 0


def finalize_web_answer(sessions, session_id, content):
    if all_web_failed(sessions, session_id):
        return (
            "Web検索・ページ取得では根拠を確認できなかったため、回答は未確認です。"
            "時間をおいて再試行するか、確認したい公開ページのURLを指定してください。"
        )
    if not content and sessions.get_session(session_id)["state"].get("web_evidence", {}).get(
        "attempted"
    ):
        return "Webツールは実行しましたが、モデルが回答を生成できませんでした。質問を絞って再試行してください。"
    return content


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
