"""The local monitor (monitor.py): what a page sees on connecting, the Pause / Resume control and its guard."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import monitor

NOW = datetime.now(timezone.utc)


def line(kind: str, minutes_ago: float = 1, **fields: object) -> str:
    return json.dumps({"t": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "kind": kind, **fields})


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.events = self.dir / "events.jsonl"

    def test_a_connecting_page_gets_the_events_worth_showing(self):
        rows = [
            line("live-sweep", 60, source="old", items=5, new=0),  # quiet and old: not sent
            line("live-sweep", 5, source="recent", items=5, new=0),  # recent: counted by the wire panel
            line("live-sweep", 50, source="busy", items=5, new=2),  # brought something: shown
            line("live-arrive", 4, title="A story"),
            line("ink-start", 3, id="a", purpose="story_summary"),
            line("ink", 3, id="a", text="done "),
            line("ink-end", 3, id="a", ok=True, chars=5),
            line("ink-start", 1, id="b", purpose="entailment"),
            line("ink", 1, id="b", text="still "),
            line("ink", 1, id="b", text="writing"),
        ]
        self.events.write_text("\n".join(rows) + "\n", encoding="utf-8")
        lines, offset = monitor.tail_events(self.events)
        sent = [json.loads(x) for x in lines]
        self.assertNotIn("old", [e.get("source") for e in sent])
        self.assertIn("recent", [e.get("source") for e in sent])
        self.assertIn("busy", [e.get("source") for e in sent])
        self.assertEqual([e["text"] for e in sent if e["kind"] == "ink"], ["done ", "still ", "writing"])  # the open call, the last done
        self.assertEqual(offset, self.events.stat().st_size)

    def test_a_half_written_last_line_waits_for_the_stream(self):
        self.events.write_bytes((line("live-arrive", title="A") + "\n" + '{"t": "x", "kind": "live-ar').encode("utf-8"))
        lines, offset = monitor.tail_events(self.events)
        self.assertEqual(len(lines), 1)
        self.assertEqual(offset, len((line("live-arrive", title="A") + "\n").encode("utf-8")))

    def test_pause_and_resume(self):
        ran, unloaded = [], []
        mon = monitor.Monitor(self.dir, run=lambda script: (ran.append(script), (0, ""))[1], unload=unloaded.append)
        monitor_models = monitor.ollama_models
        monitor.ollama_models = lambda: {"up": True, "models": [{"name": "qwen3.8:27b-q4_K_M", "vram": 1}, {"name": "someone-elses", "vram": 1}]}
        try:
            result = mon.pause()
        finally:
            monitor.ollama_models = monitor_models
        self.assertTrue(result["ok"])
        self.assertEqual(unloaded, ["qwen3.8:27b-q4_K_M"])  # only the newsroom's models
        self.assertTrue(monitor.pause_state(self.dir / "gpu_pause.json")["active"])
        self.assertIn("Disable-ScheduledTask -TaskName newsroom-live", ran[0])
        mon.resume()
        self.assertFalse((self.dir / "gpu_pause.json").exists())
        self.assertTrue(any("Enable-ScheduledTask -TaskName newsroom-live" in s for s in ran))
        kinds = [json.loads(x)["kind"] for x in self.events.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["owner-pause", "owner-resume"])

    def test_the_live_service_reads_the_same_pause_file(self):
        import live_desk

        mon = monitor.Monitor(self.dir, run=lambda script: (0, ""), unload=lambda model: None)
        mon.pause()
        self.assertEqual(mon.pause_path.name, live_desk.GPU_PAUSE)
        self.assertTrue(live_desk.gpu_paused(path=mon.pause_path))

    def test_only_this_computers_own_page_can_press_pause(self):
        ok = {"Host": "127.0.0.1:8790", "X-Monitor": "1", "Origin": "http://127.0.0.1:8790"}
        self.assertTrue(monitor.allowed(ok, 8790))
        self.assertTrue(monitor.allowed({"Host": "localhost:8790", "X-Monitor": "1"}, 8790))
        self.assertFalse(monitor.allowed({**ok, "X-Monitor": None}, 8790))  # a plain cross-site form post
        self.assertFalse(monitor.allowed({**ok, "Origin": "https://evil.example"}, 8790))
        self.assertFalse(monitor.allowed({**ok, "Host": "evil.example:8790"}, 8790))  # DNS rebinding

    def test_the_page_never_writes_event_text_as_html(self):
        page = (Path(__file__).resolve().parent.parent / "monitor" / "index.html").read_text(encoding="utf-8")
        script = page.split("<script>")[1]
        self.assertNotIn("innerHTML = e", script)
        self.assertEqual([chunk for chunk in script.split("innerHTML")[1:] if "e." in chunk.split(";")[0]], [])


if __name__ == "__main__":
    unittest.main()
