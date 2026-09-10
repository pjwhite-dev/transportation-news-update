from __future__ import annotations

import unittest

from publication import publish_briefing_to_github


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = iter(
            [
                FakeResponse({"object": {"sha": "parent"}}),
                FakeResponse({"tree": {"sha": "base-tree"}}),
                FakeResponse({"sha": "latest-blob"}),
                FakeResponse({"sha": "new-tree"}),
                FakeResponse({"sha": "new-commit"}),
                FakeResponse({"object": {"sha": "new-commit"}}),
            ]
        )

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((method, url, kwargs.get("json")))
        return next(self.responses)


class PublicationTests(unittest.TestCase):
    def test_publishes_latest_and_dated_archive_in_one_tree(self) -> None:
        session = FakeSession()
        briefing = {
            "window_end": "2026-09-10T04:15:00-04:00",
            "sections": {},
        }

        commit_sha = publish_briefing_to_github(
            briefing,
            "test-token",
            session=session,
        )

        self.assertEqual(commit_sha, "new-commit")
        tree_payload = session.calls[3][2]
        self.assertIsNotNone(tree_payload)
        self.assertEqual(
            [entry["path"] for entry in tree_payload["tree"]],
            [
                "data/latest_briefing.json",
                "data/archive/2026-09-10.json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
