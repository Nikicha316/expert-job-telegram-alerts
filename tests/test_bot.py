import json
import tempfile
import unittest
from pathlib import Path

from bot import NotificationError, format_message, load_state, monitor_source
from models import Job


class FakeNotifier:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_job(self, job):
        if self.fail:
            raise NotificationError("Temporary Telegram failure")
        self.sent.append(job.job_id)


class BotTest(unittest.TestCase):
    def test_baseline_new_job_and_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = {"version": 1, "sources": {}}
            old = Job("mercor", "list_old", "Old", "https://work.mercor.com/jobs/list_old/old", "Remote")
            new = Job("mercor", "list_new", "New", "https://work.mercor.com/jobs/list_new/new", "Remote")
            notifier = FakeNotifier()
            self.assertEqual(monitor_source("mercor", [old], state, notifier, path), (1, 0))
            self.assertEqual(notifier.sent, [])
            self.assertEqual(monitor_source("mercor", [old, new], state, notifier, path), (0, 1))
            self.assertEqual(notifier.sent, ["list_new"])
            self.assertEqual(monitor_source("mercor", [old, new], state, notifier, path), (0, 0))
            self.assertEqual(notifier.sent, ["list_new"])
            self.assertEqual(len(load_state(path)["sources"]["mercor"]["seen_ids"]), 2)

    def test_failed_message_not_marked_seen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = {"version": 1, "sources": {}}
            job1 = Job("micro1", "id1", "First", "https://jobs.micro1.ai/post/id1")
            job2 = Job("micro1", "id2", "Second", "https://jobs.micro1.ai/post/id2")
            monitor_source("micro1", [job1], state, FakeNotifier(), path)
            with self.assertRaises(NotificationError):
                monitor_source("micro1", [job1, job2], state, FakeNotifier(fail=True), path)
            self.assertEqual(load_state(path)["sources"]["micro1"]["seen_ids"], ["id1"])
            ok = FakeNotifier()
            monitor_source("micro1", [job1, job2], state, ok, path)
            self.assertEqual(ok.sent, ["id2"])

    def test_format_location_and_escape_html(self):
        job = Job("micro1", "abc", "Writer <Tester>", "https://jobs.micro1.ai/post/abc",
                  "Remote (Europe)", "€30 / hour")
        msg = format_message(job)
        self.assertIn("Writer &lt;Tester&gt;", msg)
        self.assertIn("Location:</b> Remote (Europe)", msg)
        self.assertIn("€30 / hour", msg)
        self.assertIn("View job and apply", msg)
        self.assertNotIn("<Tester>", msg)

    def test_unreadable_state_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text("not json")
            with self.assertRaises(RuntimeError):
                load_state(path)


if __name__ == "__main__":
    unittest.main()
