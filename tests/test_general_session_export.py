import base64
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_general_api import FakeOllama, FakeRegistry, secure_client
from use_lllm.core.sessions import SessionStore
from use_lllm.general.api import create_general_app


class SessionEditingExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(Path(self.temp.name))
        self.app = create_general_app(
            session_store=self.store,
            ollama_factory=lambda config: FakeOllama(),
            registry_factory=lambda specs: FakeRegistry(specs),
        )

    def test_rename_persists_and_is_scoped_to_general(self):
        session = self.store.create_session("old", surface="general")
        other = self.store.create_session("private")
        with secure_client(self.app) as client:
            url = f"/api/sessions/{session['id']}"
            response = client.patch(url, json={"title": "  自由な名前  "})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get(url).json()["title"], "自由な名前")
            self.assertTrue(client.get(url).json()["state"]["title_manually_set"])
            self.assertEqual(client.patch(url, json={"title": "  "}).status_code, 422)
            self.assertEqual(
                client.patch(f"/api/sessions/{other['id']}", json={"title": "x"}).status_code, 404
            )

    def test_export_embeds_images_all_history_and_safe_json(self):
        session = self.store.create_session("</script><script>alert(1)</script>", surface="general")
        sid = session["id"]
        data = b"\x89PNG\r\n\x1a\nexample"
        artifact = self.store.save_artifact(sid, "plot.png", data)
        name = Path(artifact["path"]).name
        self.store.add_message(sid, "user", "hello <script>alert(2)</script>")
        self.store.add_message(
            sid,
            "tool",
            "result",
            {
                "content_blocks": [
                    {"type": "artifact_image", "url": f"./api/sessions/{sid}/artifacts/{name}"},
                    {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": base64.b64encode(data).decode(),
                    },
                ]
            },
        )
        self.store.append_event(sid, "export_test", {"all": "history"})
        with secure_client(self.app) as client:
            response = client.get(f"/api/sessions/{sid}/export?language=en")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment;", response.headers["content-disposition"])
            self.assertIn('lang="en"', response.text)
            self.assertIn("data:image/png;base64," + base64.b64encode(data).decode(), response.text)
            self.assertIn("export_test", response.text)
            self.assertNotIn("<script>alert(", response.text)
            self.assertNotIn('src="./', response.text)
            self.assertIn("Plotly.newPlot", response.text)
            payload = response.text.split('<script id="session-data" type="application/json">')[
                1
            ].split("</script>")[0]
            restored = json.loads(payload)
            self.assertEqual(len(restored["messages"]), 2)
            self.assertEqual(restored["title"], session["title"])
            other = self.store.create_session("other")
            self.assertEqual(client.get(f"/api/sessions/{other['id']}/export").status_code, 404)

    def test_export_rejects_missing_and_cross_session_images(self):
        session = self.store.create_session("export", surface="general")
        other = self.store.create_session("other", surface="general")
        artifact = self.store.save_artifact(other["id"], "other.png", b"private-image")
        self.store.add_message(
            session["id"],
            "tool",
            "result",
            {
                "content_blocks": [
                    {
                        "type": "artifact_image",
                        "url": f"./api/sessions/{other['id']}/artifacts/{Path(artifact['path']).name}",
                    },
                ]
            },
        )
        with secure_client(self.app) as client:
            response = client.get(f"/api/sessions/{session['id']}/export")
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("private-image", response.text)


if __name__ == "__main__":
    unittest.main()
