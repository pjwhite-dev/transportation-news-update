from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import url_resolver as resolver


def old_style_id(url: str) -> str:
    payload = b"\x08\x13\x22" + bytes([len(url)]) + url.encode() + b"\xd2\x01\x00"
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


class DecodeTests(unittest.TestCase):
    def test_embedded_publisher_url_is_decoded_without_network(self) -> None:
        url = "https://www.example.com/news/story-1"
        wrapper = f"https://news.google.com/rss/articles/{old_style_id(url)}?oc=5"
        self.assertEqual(resolver.resolve_url(wrapper, session=Mock()), url)

    def test_non_wrapper_urls_pass_through(self) -> None:
        self.assertEqual(resolver.resolve_url("https://www.faa.gov/x"), "https://www.faa.gov/x")

    def test_new_format_id_needs_exchange_and_failures_return_none(self) -> None:
        wrapper = "https://news.google.com/rss/articles/CBMiAU_yqLabcdefgh"
        session = Mock()
        session.get.return_value = Mock(status_code=403, text="")
        self.assertIsNone(resolver.resolve_url(wrapper, session))

    def test_new_format_exchange_parses_batchexecute_response(self) -> None:
        session = Mock()
        session.get.return_value = Mock(
            status_code=200, text='<c-wiz data-n-a-sg="SIG" data-n-a-ts="123"></c-wiz>'
        )
        inner = json.dumps(["garturlresp", "https://www.publisher.com/a"])
        body = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner], ["di", 1], ["e", 2]]) + "\n\n"
        session.post.return_value = Mock(status_code=200, text=body)
        wrapper = "https://news.google.com/rss/articles/CBMiAU_yqLzzzzzzzzz"
        self.assertEqual(resolver.resolve_url(wrapper, session), "https://www.publisher.com/a")
        self.assertIn("SIG", session.post.call_args.kwargs["data"] or "SIG")


class ResolveArticlesTests(unittest.TestCase):
    def test_wrappers_are_replaced_and_failures_are_marked_unresolved(self) -> None:
        good = "https://www.example.com/a"
        articles = [
            {"url": f"https://news.google.com/rss/articles/{old_style_id(good)}"},
            {"url": "https://news.google.com/rss/articles/CBMiAU_yqLbad"},
            {"url": "https://www.faa.gov/direct"},
        ]
        failing = Mock()
        failing.get.return_value = Mock(status_code=500, text="")
        with tempfile.TemporaryDirectory() as tmp:
            stats = resolver.resolve_articles(
                articles, Path(tmp) / "cache.json", workers=1, delay_seconds=0,
                session_factory=lambda: failing,
            )
            self.assertEqual(stats, {"wrappers": 2, "resolved": 1, "unresolved": 1})
            self.assertEqual(articles[0]["url"], good)
            self.assertTrue(articles[0]["aggregator_url"].startswith("https://news.google.com"))
            self.assertTrue(articles[1]["url_unresolved"])
            self.assertEqual(articles[2]["url"], "https://www.faa.gov/direct")
            self.assertTrue((Path(tmp) / "cache.json").exists())

    def test_no_wrappers_means_no_disk_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats = resolver.resolve_articles([{"url": "https://a.com/x"}], Path(tmp) / "c.json")
            self.assertEqual(stats["wrappers"], 0)
            self.assertFalse((Path(tmp) / "c.json").exists())


if __name__ == "__main__":
    unittest.main()
