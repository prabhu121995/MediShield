"""
LLM provider tests. Ollama's HTTP API and Groq are stubbed, so these run offline.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_llm -v
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app import config, llm


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ollama_reply(content: str):
    return FakeResponse(json.dumps({"message": {"role": "assistant", "content": content}}).encode())


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            mock.patch.object(config, "LLM_PROVIDER", "ollama"),
            mock.patch.object(config, "MODEL_NAME", "gemma3:4b"),
            mock.patch.object(config, "VISION_MODEL_NAME", "gemma3:4b"),
            mock.patch.object(config, "OLLAMA_HOST", "http://127.0.0.1:11434"),
            mock.patch.object(config, "OLLAMA_THINK", None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_chat_posts_json_mode_request_and_parses_reply(self):
        with mock.patch("urllib.request.urlopen", return_value=ollama_reply('{"covered": true}')) as call:
            out = llm.chat_json("system text", "user text")
        self.assertEqual(out, {"covered": True})
        req = call.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/chat")
        body = json.loads(req.data)
        self.assertEqual(body["model"], "gemma3:4b")
        self.assertEqual(body["format"], "json")
        self.assertFalse(body["stream"])
        self.assertEqual(body["messages"][0], {"role": "system", "content": "system text"})
        self.assertNotIn("think", body)            # not sent unless OLLAMA_THINK is set
        self.assertEqual(body["options"]["num_ctx"], config.OLLAMA_NUM_CTX)   # long prompts are not cut

    def test_thinking_flag_is_sent_only_when_configured(self):
        with mock.patch.object(config, "OLLAMA_THINK", False), \
             mock.patch("urllib.request.urlopen", return_value=ollama_reply("{}")) as call:
            llm.chat_json("s", "u")
        self.assertIs(json.loads(call.call_args.args[0].data)["think"], False)

    def test_fenced_json_is_accepted(self):
        with mock.patch("urllib.request.urlopen", return_value=ollama_reply('```json\n{"a": 1}\n```')):
            self.assertEqual(llm.chat_json("s", "u"), {"a": 1})

    def test_vision_sends_a_downscaled_jpeg_in_the_images_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.png"
            Image.new("RGB", (3000, 2000), "white").save(path)
            with mock.patch("urllib.request.urlopen", return_value=ollama_reply('{"raw_text": "x"}')) as call:
                out = llm.vision_json(path, "read this")
        self.assertEqual(out, {"raw_text": "x"})
        body = json.loads(call.call_args.args[0].data)
        self.assertEqual(body["model"], "gemma3:4b")
        message = body["messages"][0]
        self.assertEqual(message["content"], "read this")
        self.assertEqual(len(message["images"]), 1)
        image = Image.open(io.BytesIO(__import__("base64").b64decode(message["images"][0])))
        self.assertLessEqual(max(image.size), config.VISION_MAX_SIDE)

    def test_unreachable_ollama_gives_a_clear_error(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")), \
             mock.patch.object(llm.time, "sleep"):
            with self.assertRaises(RuntimeError) as cm:
                llm.chat_json("s", "u")
        self.assertIn("Ollama is not reachable", str(cm.exception))


class ProviderSwitchTests(unittest.TestCase):
    def test_groq_is_used_when_provider_is_groq(self):
        fake_groq = mock.Mock()
        fake_groq.chat.completions.create.return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content='{"ok": true}'))])
        with mock.patch.object(config, "LLM_PROVIDER", "groq"), \
             mock.patch.object(llm, "_groq", return_value=fake_groq), \
             mock.patch("urllib.request.urlopen") as ollama:
            self.assertEqual(llm.chat_json("s", "u", model="qwen"), {"ok": True})
        ollama.assert_not_called()
        self.assertEqual(fake_groq.chat.completions.create.call_args.kwargs["response_format"],
                         {"type": "json_object"})

    def test_groq_vision_does_not_request_json_mode(self):
        fake_groq = mock.Mock()
        fake_groq.chat.completions.create.return_value = mock.Mock(
            choices=[mock.Mock(message=mock.Mock(content='{"raw_text": "x"}'))])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.png"
            Image.new("RGB", (100, 100), "white").save(path)
            with mock.patch.object(config, "LLM_PROVIDER", "groq"), \
                 mock.patch.object(llm, "_groq", return_value=fake_groq):
                llm.vision_json(path, "read")
        self.assertNotIn("response_format", fake_groq.chat.completions.create.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
