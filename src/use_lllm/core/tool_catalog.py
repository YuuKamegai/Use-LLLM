"""LLM に渡すツール定義を要約し、全文は組み込みツールで引けるようにする。

ms-data-parser は 72 ツールで定義が約 95KB（約 32k トークン）あり、うち 7 割が
説明文。全文を毎ターン渡すとローカル LLM ではツール定義だけで入力予算を
食い潰し、会話を要約しても空きが戻らない。

ツール選択に要るのは「何をするツールか」までなので、説明は最初の段落だけ渡す。
引数スキーマは呼び出しの正しさに直結するため削らない。省いた全文は
``use_lllm::describe_tool`` で必要なときだけ取り出させる。ツール自体は
1 件も落とさないので、「候補に見えず手順が止まる」問題は再発しない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

DESCRIBE_TOOL_NAME = "use_lllm::describe_tool"
# LLM に渡す説明の上限文字数。ms-data-parser の第1段落は最長 146 文字。
MAX_SUMMARY_CHARS = 300
# describe_tool が返す説明全文の上限。最大の arf_parser で約 4k 文字。
MAX_DESCRIBE_CHARS = 12_000


def summarize_description(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """説明の最初の段落を limit 文字以内で返す。"""

    first = text.strip().split("\n\n", 1)[0].strip()
    first = " ".join(line.strip() for line in first.splitlines())
    if len(first) <= limit:
        return first
    return first[: limit - 1].rstrip() + "…"


def _describe_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": DESCRIBE_TOOL_NAME,
            "description": (
                "MCPツールの説明は要約版です。引数の意味・前提条件・エラー時の対処など"
                "詳細が必要なときは、このツールにツール名を渡して説明全文と引数スキーマを"
                "取得してください。MCPサーバーへは問い合わせません。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "server::tool 形式のツール名",
                    }
                },
                "required": ["name"],
            },
        },
    }


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    """1 ターン分の LLM 向けツール一覧と、要約前の全文。"""

    tools: list[dict[str, Any]]
    full: dict[str, dict[str, Any]]

    @classmethod
    def build(cls, tools: list[dict[str, Any]]) -> "ToolCatalog":
        compact: list[dict[str, Any]] = []
        full: dict[str, dict[str, Any]] = {}
        shortened = False
        for item in tools:
            function = item.get("function") or {}
            name = str(function.get("name", ""))
            description = str(function.get("description") or "")
            summary = summarize_description(description)
            shortened = shortened or summary != description.strip()
            full[name] = function
            compact.append({**item, "function": {**function, "description": summary}})
        if shortened:
            compact.append(_describe_tool_definition())
        return cls(compact, full)

    def describe(self, arguments: dict[str, Any]) -> tuple[str, bool]:
        """describe_tool の結果本文と、エラーかどうかを返す。"""

        name = str(arguments.get("name", "")).strip()
        function = self.full.get(name)
        if function is None:
            # tool 部分だけ指定された場合は末尾一致で引く（SYSTEM_PROMPT と同じ規則）。
            matches = [key for key in self.full if key.split("::", 1)[-1] == name]
            if len(matches) == 1:
                name, function = matches[0], self.full[matches[0]]
        if function is None:
            return (
                f"ツール {name or '(未指定)'} は現在利用可能なツールにありません。"
                "利用可能なツール名を server::tool 形式で指定してください。",
                True,
            )
        description = str(function.get("description") or "")[:MAX_DESCRIBE_CHARS]
        return (
            json.dumps(
                {
                    "name": name,
                    "description": description,
                    "parameters": function.get("parameters") or {},
                },
                ensure_ascii=False,
            ),
            False,
        )
