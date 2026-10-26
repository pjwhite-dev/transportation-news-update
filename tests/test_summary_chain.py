from __future__ import annotations

import unittest
from types import SimpleNamespace

import summary_chain as chain
from ai_providers import StructuredGeneration, TextGeneration


def sections(count: int = 6) -> dict:
    items = [
        {
            "id": f"x{i}", "title": f"Story {i} headline", "summary": f"Story {i} happened.", "source": "Reuters",
            "url": f"https://example.com/story-{i}", "importance": 10 - i, "date_label": "Oct. 25, 2026",
        }
        for i in range(1, count + 1)
    ]
    return {"Autonomous Vehicles": items[:3], "UAS / Drones": items[3:]}


def draft(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


GOOD = draft(
    "The Federal Aviation Administration cleared the first commercial passenger demonstration flight of the electric aircraft on Friday after months of testing in the Texas desert [S1]. "
    "The National Highway Traffic Safety Administration proposed new exterior lighting rules for driverless vehicles that would let robotaxis signal when the system is in control [S2]. "
    "Zoox recalled its entire fleet of 270 driverless vehicles after a crash in Las Vegas earlier this month [S3].",
    "Archer Aviation announced plans to open air taxi networks in Houston, Dallas and Austin, with launch dates still to be set by the company [S4]. "
    "Boom Supersonic opened a 150 million dollar factory expansion that the company says could create 500 jobs over the next several years [S5][S6]. "
    "Regulators in France set a 2027 approval path for autonomous vehicles, with pilot programs planned for several cities [S6].",
)


class FakeWriter:
    name, model = "fake-writer", "qwen3.8:27b-q4_K_M"

    def __init__(self, drafts):
        self.drafts, self.calls = list(drafts), []

    def generate_text(self, messages, *, max_output_tokens):
        self.calls.append(messages)
        text = self.drafts.pop(0)
        return TextGeneration(text, {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}, 0.0, 0)


class FakeVerifier:
    name, model = "fake-verifier", "gemma4:31b"

    def __init__(self, flag_if=None):
        self.flag_if = flag_if or (lambda sentence: None)
        self.calls = 0

    def generate_structured(self, messages, *, schema_name, schema, max_output_tokens):
        self.calls += 1
        body = messages[-1]["content"]
        listing = body.split("SENTENCES TO CHECK\n", 1)[1].splitlines()
        out = []
        for line in listing:
            number, sentence = line.split(". ", 1)
            verdict = self.flag_if(sentence)
            out.append({"n": int(number), "verdict": verdict or "supported", "note": verdict or "ok"})
        return StructuredGeneration({"sentences": out}, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}, 0.0, 0)


class HelperTests(unittest.TestCase):
    def test_fact_sheet_orders_by_importance_and_numbers_stories(self) -> None:
        sheet = chain.build_fact_sheet(sections())
        self.assertEqual([s["id"] for s in sheet], ["S1", "S2", "S3", "S4", "S5", "S6"])
        self.assertEqual(sheet[0]["url"], "https://example.com/story-1")

    def test_citations_become_numbered_real_links_in_order_of_first_use(self) -> None:
        sheet = chain.build_fact_sheet(sections())
        text = chain.link_citations("A [S3]. B [S1][S3]. C [S2, S1]. D [S99].", sheet)
        self.assertEqual(
            text,
            "A [1](https://example.com/story-3). B [2](https://example.com/story-1)[1](https://example.com/story-3). "
            "C [3](https://example.com/story-2)[2](https://example.com/story-1). D.",
        )

    def test_sentences_split_after_markers_and_report_their_ids(self) -> None:
        parts = chain.split_sentences("First thing happened [S1]. Second thing [S2][S3]. Third.")
        self.assertEqual(len(parts), 3)
        self.assertEqual(chain.cited_ids(parts[1]), ["S2", "S3"])
        self.assertEqual(chain.strip_markers(parts[1]), "Second thing.")


class ChainTests(unittest.TestCase):
    def test_clean_draft_passes_with_real_links_and_provenance(self) -> None:
        writer, verifier = FakeWriter([GOOD]), FakeVerifier()
        result = chain.run_chain(writer, verifier, sections())
        self.assertEqual(result.text.count("](https://example.com/story-"), 7)
        self.assertNotIn("[S", result.text)
        self.assertEqual(len(result.text.split("\n\n")), 2)
        v = result.verification
        self.assertEqual((v["verified_by"], v["writer"], v["sentences_dropped"], v["unsupported_remaining"]),
                         ("gemma4:31b", "qwen3.8:27b-q4_K_M", 0, 0))
        self.assertEqual(len(v["rounds"]), 1)
        self.assertEqual(result.usage["total_tokens"], 40)

    def test_uncited_or_unknown_id_sentence_is_flagged_and_rewritten(self) -> None:
        bad = GOOD.replace("earlier this month [S3].", "earlier this month [S99].")
        writer, verifier = FakeWriter([bad, GOOD]), FakeVerifier()
        result = chain.run_chain(writer, verifier, sections())
        self.assertEqual(len(writer.calls), 2)
        self.assertIn("flagged these sentences", writer.calls[1][-1]["content"])
        self.assertEqual(result.verification["rounds"][0]["flagged"], 1)
        self.assertEqual(result.verification["sentences_dropped"], 0)

    def test_unsupported_sentence_is_revised_then_passes(self) -> None:
        interpretive = GOOD.replace("over the next several years [S5][S6].", "over the next several years [S5]. This signals sustained investment [S5].")
        verifier = FakeVerifier(lambda s: "unsupported" if "signals" in s else None)
        writer = FakeWriter([interpretive, GOOD])
        result = chain.run_chain(writer, verifier, sections())
        self.assertNotIn("signals", result.text)
        self.assertEqual(len(result.verification["rounds"]), 2)

    def test_persistent_problem_sentences_are_dropped_by_code_never_published(self) -> None:
        stubborn = GOOD + " This reflects a broader trend [S1]."
        verifier = FakeVerifier(lambda s: "unsupported" if "broader trend" in s else None)
        writer = FakeWriter([stubborn, stubborn, stubborn])
        result = chain.run_chain(writer, verifier, sections())
        self.assertNotIn("broader trend", result.text)
        self.assertEqual(result.verification["sentences_dropped"], 1)
        self.assertEqual(result.verification["unsupported_remaining"], 0)
        self.assertEqual(len(writer.calls), chain.MAX_REVISIONS + 1)

    def test_status_errors_are_blocking_too(self) -> None:
        wrong = GOOD.replace("proposed new exterior lighting rules", "finalized new exterior lighting rules")
        verifier = FakeVerifier(lambda s: "status_error" if "finalized" in s else None)
        result = chain.run_chain(FakeWriter([wrong, GOOD]), verifier, sections())
        self.assertNotIn("finalized", result.text)
        self.assertEqual(result.verification["rounds"][0]["status_errors"], 1)

    def test_fragments_are_flagged_rewritten_or_dropped(self) -> None:
        fragment = GOOD.replace("over the next several years [S5][S6].", "over the next several years [S5][S6]. Army to intercept drones [S3].")
        verifier = FakeVerifier(lambda s: "fragment" if s.startswith("Army to intercept") else None)
        result = chain.run_chain(FakeWriter([fragment, fragment, fragment]), verifier, sections())
        self.assertNotIn("Army to intercept", result.text)
        self.assertEqual(result.verification["sentences_dropped"], 1)

    def test_very_short_sentences_are_treated_as_fragments_even_if_the_verifier_agrees(self) -> None:
        short = GOOD.replace("over the next several years [S5][S6].", "over the next several years [S5][S6]. Army tests drones [S3].")
        result = chain.run_chain(FakeWriter([short, GOOD]), FakeVerifier(), sections())
        self.assertNotIn("Army tests drones", result.text)
        self.assertEqual(result.verification["rounds"][0]["fragments"], 1)

    def test_too_little_verified_text_fails_the_run(self) -> None:
        verifier = FakeVerifier(lambda s: "unsupported")
        with self.assertRaisesRegex(chain.SummaryChainError, "refusing to publish"):
            chain.run_chain(FakeWriter([GOOD] * 3), verifier, sections())

    def test_fewer_than_three_stories_fails_clearly(self) -> None:
        with self.assertRaisesRegex(chain.SummaryChainError, "Fewer than three"):
            chain.run_chain(FakeWriter([GOOD]), FakeVerifier(), {"UAS / Drones": sections()["UAS / Drones"][:2]})


class IntegrationTests(unittest.TestCase):
    def test_generate_final_executive_summary_uses_the_chain_and_records_verification(self) -> None:
        import news_engine
        from datetime import datetime
        from unittest.mock import patch

        fake = chain.ChainResult(
            text="One [1](https://example.com/story-1). Two [2](https://example.com/story-2).",
            usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            verification={"verified_by": "gemma4:31b", "rounds": [{"attempt": 0}, {"attempt": 1}]},
        )
        with patch("summary_chain.run_chain", return_value=fake):
            text, usage, cost = news_engine.generate_final_executive_summary(
                sections(), [], [], "", "m", datetime.now(), datetime.now(), SimpleNamespace(name="x", model="m"),
                verifier=SimpleNamespace(),
            )
        self.assertIn("[1](https://example.com/story-1)", text)
        self.assertEqual(usage["_summary_verification"]["verified_by"], "gemma4:31b")
        self.assertEqual(usage["_retries"], 1)
        self.assertEqual(cost, 0.0)

    def test_paragraphs_survive_sanitising(self) -> None:
        import news_engine

        text = "One [1](https://a.example/x). Two.\n\nThree [2](https://b.example/y)."
        self.assertEqual(news_engine.sanitize_executive_summary_citations(text), text)
        self.assertEqual(news_engine.sanitize_compiled_executive_summary(text, {}).count("\n\n"), 1)


if __name__ == "__main__":
    unittest.main()
