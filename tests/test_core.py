import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bigtechtea.config import ConfigError, _validate_sub          # noqa: E402
from bigtechtea.feeds import FetchError, clean_text, parse_date, parse_feed  # noqa: E402
from bigtechtea.matcher import matches                            # noqa: E402
from bigtechtea.models import Paper                               # noqa: E402
from bigtechtea.state import State                                # noqa: E402
from bigtechtea.summarize import Brief, heuristic_brief, split_sentences  # noqa: E402

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <item>
      <title>Scaling sparse mixture-of-experts</title>
      <link>https://example.com/posts/moe?utm_source=rss</link>
      <description>&lt;p&gt;We train a &lt;b&gt;MoE&lt;/b&gt; model.&lt;/p&gt;</description>
      <pubDate>Tue, 11 Aug 2026 09:00:00 GMT</pubDate>
      <dc:creator>Ada Lovelace</dc:creator>
      <category>efficiency</category>
    </item>
  </channel>
</rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Agentic tool use in long-horizon tasks</title>
    <link rel="alternate" href="https://arxiv.org/abs/2608.01234v2"/>
    <id>http://arxiv.org/abs/2608.01234v2</id>
    <summary>An agent that plans over tools.</summary>
    <published>2026-08-12T04:00:00Z</published>
    <author><name>Grace Hopper</name></author>
    <category term="cs.CL"/>
  </entry>
</feed>"""

ABSTRACT = (
    "Large language models solve tasks by calling external tools, but planning over "
    "long horizons remains challenging because errors compound. Prior work relies on "
    "hand-written prompts that do not transfer to unseen toolsets. We introduce "
    "ToolPlan, a reinforcement learning framework that interleaves tool calls with "
    "self-verification. We show a 14% absolute improvement in task success over the "
    "strongest baseline. We release code, and note the approach degrades beyond "
    "twenty steps."
)


class TestFeeds(unittest.TestCase):
    def test_rss(self):
        (entry,) = parse_feed(RSS)
        self.assertEqual(entry["title"], "Scaling sparse mixture-of-experts")
        self.assertEqual(entry["summary"], "We train a MoE model.")
        self.assertEqual(entry["authors"], ["Ada Lovelace"])
        self.assertEqual(entry["published"].year, 2026)

    def test_atom(self):
        (entry,) = parse_feed(ATOM)
        self.assertEqual(entry["url"], "https://arxiv.org/abs/2608.01234v2")
        self.assertEqual(entry["tags"], ["cs.CL"])

    def test_malformed_raises(self):
        with self.assertRaises(FetchError):
            parse_feed(b"<not xml")

    def test_clean_text_truncates(self):
        self.assertTrue(clean_text("word " * 500).endswith("\u2026"))

    def test_dates(self):
        self.assertIsNotNone(parse_date("2026-08-12"))
        self.assertIsNotNone(parse_date("Tue, 11 Aug 2026 09:00:00 GMT"))
        self.assertIsNone(parse_date("not a date"))


class TestModel(unittest.TestCase):
    def _p(self, url):
        return Paper(source="s", org="O", title="t", url=url)

    def test_uid_ignores_tracking_and_version(self):
        self.assertEqual(self._p("https://arxiv.org/abs/2608.01234v2").uid,
                         self._p("https://arxiv.org/pdf/2608.01234").uid)
        self.assertEqual(self._p("https://arxiv.org/abs/2608.01234").uid,
                         self._p("https://arxiv.org/abs/2608.01234?utm_source=x").uid)

    def test_age(self):
        old = Paper(source="s", org="O", title="t", url="u",
                    published=datetime.now(timezone.utc) - timedelta(days=10))
        self.assertGreater(old.age_days, 9)


class TestMatcher(unittest.TestCase):
    def setUp(self):
        self.paper = Paper(source="deepmind", org="Google DeepMind",
                           title="Retrieval augmented agents",
                           summary="We study RAG for long context planning.",
                           url="https://example.com/x", tags=["cs.CL"])

    def test_any_of(self):
        ok, why = matches(self.paper, {"any_of": ["rag", "diffusion"]})
        self.assertTrue(ok)
        self.assertIn("rag", why)

    def test_word_boundary(self):
        p = Paper(source="s", org="O", title="Storage systems", url="u")
        self.assertFalse(matches(p, {"any_of": ["rag"]})[0])

    def test_all_of_and_none_of(self):
        self.assertTrue(matches(self.paper, {"all_of": ["agents", "planning"]})[0])
        self.assertFalse(matches(self.paper, {"any_of": ["rag"], "none_of": ["agents"]})[0])

    def test_source_scope(self):
        self.assertFalse(matches(self.paper, {"sources": ["openai"], "any_of": ["rag"]})[0])
        self.assertTrue(matches(self.paper, {"sources": ["deepmind"], "any_of": ["rag"]})[0])

    def test_title_only(self):
        self.assertFalse(matches(self.paper,
                                 {"any_of": ["long context"], "title_only": True})[0])


class TestConfig(unittest.TestCase):
    def _sub(self, **over):
        base = {"name": "x", "ntfy_topic": "a1b2c3d4e5f6",
                "interests": {"any_of": ["rag"]}}
        base.update(over)
        return base

    def test_ntfy_topic_separate_from_interests(self):
        sub = _validate_sub(self._sub(), "alice.yml", {"openai"})
        self.assertEqual(sub["ntfy_topic"], "a1b2c3d4e5f6")
        self.assertEqual(sub["interests"]["any_of"], ["rag"])

    def test_missing_ntfy_topic(self):
        with self.assertRaises(ConfigError):
            _validate_sub({"name": "x", "interests": {"any_of": ["rag"]}},
                          "a.yml", set())

    def test_guessable_topic_rejected(self):
        for weak in ("change-me-please", "papers", "arxiv"):
            with self.assertRaises(ConfigError):
                _validate_sub(self._sub(ntfy_topic=weak), "a.yml", set())

    def test_short_topic_rejected(self):
        with self.assertRaises(ConfigError):
            _validate_sub(self._sub(ntfy_topic="abc12"), "a.yml", set())

    def test_empty_interests_rejected(self):
        with self.assertRaises(ConfigError):
            _validate_sub(self._sub(interests={}), "a.yml", set())

    def test_flat_rules_still_accepted(self):
        sub = _validate_sub({"name": "x", "ntfy_topic": "a1b2c3d4e5f6",
                             "any_of": ["rag"]}, "a.yml", set())
        self.assertEqual(sub["interests"]["any_of"], ["rag"])

    def test_unknown_source_rejected(self):
        with self.assertRaises(ConfigError):
            _validate_sub(self._sub(interests={"any_of": ["x"], "sources": ["nope"]}),
                          "a.yml", {"openai"})

    def test_scalar_where_list_expected(self):
        with self.assertRaises(ConfigError):
            _validate_sub(self._sub(interests={"any_of": "rag"}), "a.yml", set())


class TestSummarizer(unittest.TestCase):
    def test_slots_filled_from_structured_abstract(self):
        brief = heuristic_brief("ToolPlan", ABSTRACT)
        self.assertIn("compound", brief.problem)
        self.assertIn("Prior work", brief.prior_work)
        self.assertIn("ToolPlan", brief.approach)
        self.assertIn("14%", brief.results)
        self.assertIn("degrades", brief.next_steps)

    def test_no_sentence_reused_across_slots(self):
        brief = heuristic_brief("ToolPlan", ABSTRACT)
        values = [v for _, v in brief.filled()]
        self.assertEqual(len(values), len(set(values)))

    def test_short_blurb_gets_overview_not_fake_structure(self):
        brief = heuristic_brief("Launch", "Today we're sharing a small model. It runs on a phone.")
        self.assertTrue(brief.overview)
        self.assertFalse(brief.results)
        self.assertIn("blurb", " ".join(brief.notes))

    def test_missing_abstract_is_honest(self):
        brief = heuristic_brief("T", "")
        self.assertIn("no abstract", brief.as_bullets())

    def test_prior_work_gap_is_flagged_not_invented(self):
        brief = heuristic_brief("T", (
            "Training large models is expensive and slow to converge. "
            "We propose a new optimizer that adapts the learning rate per layer. "
            "We reduce wall-clock training time by 22% on ImageNet."))
        self.assertEqual(brief.prior_work, "")
        self.assertIn("prior work", " ".join(brief.notes))

    def test_sentence_split_handles_abbreviations(self):
        self.assertEqual(
            len(split_sentences("We follow Smith et al. in this setup and extend it here. "
                                "Then we evaluate on three tasks with strong baselines.")), 2)

    def test_brief_roundtrip(self):
        brief = heuristic_brief("ToolPlan", ABSTRACT)
        self.assertEqual(Brief.from_dict(brief.to_dict()).results, brief.results)


class TestState(unittest.TestCase):
    def test_roundtrip_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            state = State(path)
            self.assertTrue(state.is_new_subscription("a:b"))
            state.mark("a:b", "uid1")
            state.save()

            reloaded = State(path)
            self.assertTrue(reloaded.was_notified("a:b", "uid1"))
            self.assertFalse(reloaded.was_notified("a:b", "uid2"))
            reloaded.data["subs"]["a:b"]["uid1"] = 0
            self.assertEqual(reloaded.prune(), 1)

    def test_corrupt_file_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            path.write_text("{not json")
            self.assertEqual(State(path).data["subs"], {})


if __name__ == "__main__":
    unittest.main()


class TestContentType(unittest.TestCase):
    def _paper(self, content):
        return Paper(source="s", org="O", title="Agentic retrieval", url="u",
                     summary="rag agents", content=content)

    def test_paper_only_filters_blogs(self):
        rules = {"any_of": ["rag"], "content": "paper"}
        self.assertTrue(matches(self._paper("paper"), rules)[0])
        self.assertFalse(matches(self._paper("blog"), rules)[0])

    def test_any_content_matches_both(self):
        rules = {"any_of": ["rag"], "content": "any"}
        self.assertTrue(matches(self._paper("blog"), rules)[0])
        self.assertTrue(matches(self._paper("paper"), rules)[0])

    def test_default_is_unfiltered(self):
        self.assertTrue(matches(self._paper("blog"), {"any_of": ["rag"]})[0])

    def test_bad_content_value_rejected(self):
        with self.assertRaises(ConfigError):
            _validate_sub({"name": "x", "ntfy_topic": "a1b2c3d4e5f6",
                           "interests": {"any_of": ["rag"], "content": "papers"}},
                          "a.yml", set())

    def test_content_survives_serialisation(self):
        self.assertEqual(self._paper("paper").to_dict()["content"], "paper")


class TestSecretChannel(unittest.TestCase):
    """The ntfy channel can live in an env var so a public repo never holds it."""

    def _sub(self):
        return {"name": "x", "ntfy_topic_env": "NTFY_TOPIC_TEST",
                "interests": {"any_of": ["rag"]}}

    def test_resolves_from_environment(self):
        import os
        os.environ["NTFY_TOPIC_TEST"] = "a1b2c3d4e5f6"
        try:
            sub = _validate_sub(self._sub(), "salma.yml", set())
            self.assertEqual(sub["ntfy_topic"], "a1b2c3d4e5f6")
            self.assertTrue(sub.get("_from_env"))
            self.assertIsNone(sub.get("_unresolved"))
        finally:
            del os.environ["NTFY_TOPIC_TEST"]

    def test_unset_variable_is_skipped_not_fatal(self):
        import os
        os.environ.pop("NTFY_TOPIC_TEST", None)
        sub = _validate_sub(self._sub(), "salma.yml", set())
        self.assertEqual(sub["_unresolved"], "NTFY_TOPIC_TEST")
        self.assertIsNone(sub["ntfy_topic"])

    def test_weak_value_still_rejected(self):
        import os
        os.environ["NTFY_TOPIC_TEST"] = "papers"
        try:
            with self.assertRaises(ConfigError):
                _validate_sub(self._sub(), "salma.yml", set())
        finally:
            del os.environ["NTFY_TOPIC_TEST"]

    def test_both_forms_rejected(self):
        sub = self._sub()
        sub["ntfy_topic"] = "a1b2c3d4e5f6"
        with self.assertRaises(ConfigError):
            _validate_sub(sub, "salma.yml", set())
