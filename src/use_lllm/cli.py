"""汎用チャット WebUI のコマンドライン入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Sequence

from use_lllm.core.config import ConfigurationError, MCPServerConfig, OllamaConfig
from use_lllm.core.mcp_client import (
    READ_ONLY_SMOKE_TOOLS,
    MCPClient,
    MCPConnectionError,
    MCPServerSnapshot,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="use-lllm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke = subparsers.add_parser("mcp-smoke", help="MCPを初期化し、利用可能な全ツールを取得する")
    smoke.add_argument("--json", action="store_true", help="結果を機械可読JSONで表示する")
    smoke.add_argument(
        "--expected-tools",
        type=int,
        help="ツール数が指定値と異なる場合に終了コード3を返す",
    )

    readonly = subparsers.add_parser(
        "mcp-readonly-smoke", help="許可済みread-only MCPツールを1つ実行する"
    )
    readonly.add_argument(
        "--tool",
        choices=sorted(READ_ONLY_SMOKE_TOOLS),
        default="list_reports",
        help="実行する診断ツール（既定: list_reports）",
    )
    readonly.add_argument("--directory", help="list_data_filesへ渡すローカルディレクトリ")
    readonly.add_argument("--extension", help="list_data_filesで絞り込む拡張子")
    readonly.add_argument("--json", action="store_true", help="結果を機械可読JSONで表示する")

    ollama = subparsers.add_parser(
        "ollama-smoke", help="Ollamaのモデル存在とtool callingを確認する"
    )
    ollama.add_argument("--model", help="検証するモデル名（既定は設定値）")
    ollama.add_argument("--json", action="store_true")

    serve = subparsers.add_parser("serve", help="loopback限定WebUIを起動する")
    serve.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="起動後に既定ブラウザを開く")
    return parser


def _render_human(snapshot: MCPServerSnapshot) -> str:
    lines = [
        "MCP接続: 成功",
        f"サーバー: {snapshot.server_name} {snapshot.server_version}",
        f"プロトコル: {snapshot.protocol_version}",
        f"取得ツール数: {snapshot.tool_count}",
        "ツール:",
    ]
    lines.extend(f"  - {tool.name}" for tool in snapshot.tools)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            print("ポートは1〜65535で指定してください。", file=sys.stderr)
            return 2
        if args.open:
            import threading
            import webbrowser

            threading.Timer(
                1.2,
                webbrowser.open,
                args=(f"http://{args.host}:{args.port}/general/",),
            ).start()
        import uvicorn

        uvicorn.run("use_lllm.app:app", host=args.host, port=args.port, log_level="info")
        return 0

    if args.command == "ollama-smoke":
        from use_lllm.core.ollama import OllamaClient, OllamaError

        try:
            config = OllamaConfig.from_env()
            client = OllamaClient(config)
            result = asyncio.run(client.tool_call_smoke(args.model))
            calls = result.get("tool_calls", [])
            ok = bool(calls and calls[0].get("function", {}).get("name") == "list_data_files")
            result["passed"] = ok
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"Ollama tool calling: {'成功' if ok else '失敗'}\n{result}")
            return 0 if ok else 4
        except (ConfigurationError, OllamaError) as exc:
            print(f"Ollama検証: 失敗\n{exc}", file=sys.stderr)
            return 1

    try:
        config = MCPServerConfig.from_env()
        client = MCPClient(config)
        if args.command == "mcp-smoke":
            snapshot = asyncio.run(client.inspect_server())
        elif args.command == "mcp-readonly-smoke":
            arguments: dict[str, str] = {}
            if args.tool == "list_data_files":
                if args.directory is not None:
                    arguments["directory"] = args.directory
                if args.extension is not None:
                    arguments["extension"] = args.extension
            elif args.directory is not None or args.extension is not None:
                print("--directory/--extension はlist_data_files専用です", file=sys.stderr)
                return 2
            result = asyncio.run(client.call_read_only_smoke_tool(args.tool, arguments))
        else:
            return 2
    except (ConfigurationError, MCPConnectionError) as exc:
        print(f"MCP接続: 失敗\n{exc}", file=sys.stderr)
        cause = exc.__cause__
        if cause is not None:
            print(f"原因: {type(cause).__name__}: {cause}", file=sys.stderr)
        return 1

    if args.command == "mcp-readonly-smoke":
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"read-only MCPツール: {result.tool_name}")
            print(f"実行結果: {'失敗' if result.is_error else '成功'}")
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 4 if result.is_error else 0

    if args.json:
        print(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_human(snapshot))

    if args.expected_tools is not None and snapshot.tool_count != args.expected_tools:
        print(
            "MCPツール数が期待値と一致しません: "
            f"expected={args.expected_tools}, actual={snapshot.tool_count}",
            file=sys.stderr,
        )
        return 3
    return 0
