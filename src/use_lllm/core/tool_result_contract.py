"""MCP ツール結果の契約リーダー。

サーバが「前提となるセッション状態が無い」ことを機械可読に伝えてきたときに、
それを読み取るだけのモジュール。**特定のサーバ名もツール名も知らない。**
契約を実装しないサーバの結果は素通しになるので、後方互換は保たれる。

契約の形式は、ツール結果が {"error": {"code": "missing_state", "state": "...",
"required_tools": [...], "message": "..."}} の JSON オブジェクトを含むテキスト。
state はサーバが決める不透明な識別子。required_tools は OR の代替候補で、
先頭ほど優先される。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

MISSING_STATE_CODE = "missing_state"


@dataclass(frozen=True, slots=True)
class MissingState:
    """ツールが必要とする前提状態が無かったことの表明。

    state はサーバが決める不透明な識別子。クライアントは解釈も比較もせず、
    ログと開示にだけ使う（ここでドメイン語彙を解釈し始めると、汎用クライアントで
    なくなる）。required_tools は AND チェーンではなく **OR の代替候補**で、
    先頭ほど優先される。
    """

    state: str
    required_tools: tuple[str, ...]
    message: str


def _candidate_payloads(result_text: str):
    """本文そのものと、本文に埋め込まれた最初の JSON オブジェクトを順に試す。

    FastMCP は例外を "Error executing tool <name>: " で前置するため、本文全体が
    JSON にならないことがある。前置を落として読み直せるようにする。
    """
    yield result_text
    start = result_text.find("{")
    end = result_text.rfind("}")
    if 0 <= start < end:
        yield result_text[start : end + 1]


def read_missing_state(result_text: str | None) -> MissingState | None:
    """本文が missing_state エンベロープならそれを返す。違えば None。

    壊れた JSON、別の error code、必須フィールド欠落はすべて None。例外は投げない
    （契約を実装しないサーバの通常の出力が大量に流れてくる経路なので、
    ここで落ちると全ツール呼び出しが壊れる）。

    型チェック（isinstance）が必要な理由：falsy 値のみをフィルタすると、
    True や {"a": 1} などの truthy な非文字列が _candidate_payloads に
    到達し、.find() メソッドが存在しないため AttributeError が発生する。
    """
    if not isinstance(result_text, str) or not result_text:
        return None
    for candidate in _candidate_payloads(result_text):
        found = _read_one(candidate)
        if found is not None:
            return found
    return None


def _read_one(candidate: str) -> MissingState | None:
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("code") != MISSING_STATE_CODE:
        return None
    state = error.get("state")
    tools = error.get("required_tools")
    message = error.get("message")
    if not isinstance(state, str) or not state:
        return None
    if not isinstance(message, str) or not message:
        return None
    if not isinstance(tools, list) or not tools:
        return None
    if not all(isinstance(item, str) and item for item in tools):
        return None
    return MissingState(state, tuple(tools), message)
