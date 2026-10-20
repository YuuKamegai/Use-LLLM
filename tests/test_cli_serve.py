from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import patch

from use_lllm import cli


def _occupy() -> socket.socket:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    return holder


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class WebSocketBindingTests(unittest.TestCase):
    def test_busy_preferred_port_falls_back_to_a_free_port(self) -> None:
        # 既定ポートを別アプリが使っていても、WebUI は起動できなければならない。
        with _occupy() as holder:
            busy = holder.getsockname()[1]
            with cli._bind_web_socket("127.0.0.1", None, preferred=busy) as sock:
                self.assertNotEqual(sock.getsockname()[1], busy)

    def test_free_preferred_port_is_kept(self) -> None:
        port = _free_port()
        with cli._bind_web_socket("127.0.0.1", None, preferred=port) as sock:
            self.assertEqual(sock.getsockname()[1], port)

    def test_explicit_busy_port_is_an_error(self) -> None:
        # 明示されたポートを黙って変えると、指定した URL で開けなくなる。
        with _occupy() as holder:
            busy = holder.getsockname()[1]
            with self.assertRaises(OSError):
                cli._bind_web_socket("127.0.0.1", busy, preferred=busy)


class ServeCommandTests(unittest.TestCase):
    def test_serve_runs_on_the_bound_socket_and_publishes_its_port(self) -> None:
        # OAuth の redirect_uri は USE_LLLM_WEB_PORT から組むので、実ポートと一致させる。
        captured: dict[str, object] = {}

        def fake_run(self, sockets=None):
            captured["port"] = sockets[0].getsockname()[1]
            captured["env"] = os.environ.get("USE_LLLM_WEB_PORT")

        with _occupy() as holder, patch.dict(os.environ, {}, clear=False):
            busy = holder.getsockname()[1]
            with patch.object(cli, "DEFAULT_WEB_PORT", busy), patch(
                "uvicorn.Server.run", fake_run
            ):
                self.assertEqual(cli.main(["serve"]), 0)

        self.assertNotEqual(captured["port"], busy)
        self.assertEqual(captured["env"], str(captured["port"]))


if __name__ == "__main__":
    unittest.main()
