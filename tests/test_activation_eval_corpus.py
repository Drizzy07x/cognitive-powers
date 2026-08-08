"""The corpus half of the activation eval: the YAML subset and the case rules."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "evals") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "evals"))

from activation_core import yamlite  # noqa: E402
from tests.activation_eval_support import TempTreeTestCase  # noqa: E402
from activation_core.cases import (  # noqa: E402
    Case,
    CorpusError,
    load_corpus,
    load_file,
    select,
)

SKILLS = frozenset(
    {"diagnose-systematically", "refactor-cleanly", "verify-delivery", "map-project"}
)

VALID = """
version: 1
suite: should-fire
cases:
  - id: diagnose-click
    prompt: "the login button does nothing"
    lang: en
    expect:
      - diagnose-systematically
    forbid: []
    fixture: webshop
    quick: true
  - id: quiet-rename
    prompt: rename this variable from x to count
    expect: []
    forbid:
      - refactor-cleanly
    fixture: pylib
"""


class YamliteTests(TempTreeTestCase):
    def test_parses_the_shape_a_case_file_actually_uses(self) -> None:
        document = yamlite.loads(VALID)
        self.assertEqual(document["version"], 1)
        self.assertEqual(len(document["cases"]), 2)
        self.assertEqual(document["cases"][0]["quick"], True)
        self.assertEqual(document["cases"][0]["expect"], ["diagnose-systematically"])
        self.assertEqual(document["cases"][1]["expect"], [])

    def test_a_hash_only_opens_a_comment_at_a_token_boundary(self) -> None:
        # The YAML rule, and the reason every prompt in the corpus is written
        # quoted: a spaced '#' really does end a plain scalar mid-sentence, so
        # an unquoted prompt could silently become a shorter prompt.
        document = yamlite.loads("a: bug#1 stays  # this goes\nb: bug #1 truncates\n")
        self.assertEqual(document["a"], "bug#1 stays")
        self.assertEqual(document["b"], "bug")

    def test_quoted_scalars_keep_their_punctuation(self) -> None:
        document = yamlite.loads("a: \"why: because #7\"\nb: 'it''s fine'\n")
        self.assertEqual(document["a"], "why: because #7")
        self.assertEqual(document["b"], "it's fine")

    def test_a_sequence_may_sit_at_its_own_key_indent(self) -> None:
        """The commonest way anyone writes a list, and the one shape that failed.

        The guard treated a same-indent dash line as "this key has no value",
        so the dash lines then fell out of the mapping loop and the document
        died with "unexpected indentation" -- pointing at the list rather than
        at the rule that refused it. The parser's own comment claimed to
        support this shape.
        """
        self.assertEqual(
            {"skills": ["alpha", "beta"], "mode": "fast"},
            yamlite.loads("skills:\n- alpha\n- beta\nmode: fast\n"),
        )
        self.assertEqual(
            {"cases": [{"id": "a", "prompt": "p"}, {"id": "b", "prompt": "q"}]},
            yamlite.loads("cases:\n- id: a\n  prompt: p\n- id: b\n  prompt: q\n"),
        )

    def test_backslash_escapes_are_read_once_left_to_right(self) -> None:
        r"""Chained replaces re-read a character an earlier pass had written.

        ``"a\\nb"`` is a literal backslash followed by ``n``. The first pass
        rewrote that ``\n`` into a newline before the pass meant to collapse
        ``\\`` could see it, so the value came back one character short and
        carrying a line break the author never wrote.
        """
        backslash = chr(92)
        for inner, expected in (
            (backslash * 2 + "n", backslash + "n"),
            (backslash + "n", "\n"),
            (backslash + '"', '"'),
            (backslash + "t", "\t"),
            (backslash + "q", backslash + "q"),
        ):
            with self.subTest(inner=inner):
                document = yamlite.loads('p: "a' + inner + 'b"\n')
                self.assertEqual("a" + expected + "b", document["p"])

    def test_an_escaped_quote_does_not_end_the_scalar_before_a_comment(self) -> None:
        """A prompt containing a quotation mark used to fail to load at all."""
        backslash = chr(92)
        document = yamlite.loads(f'p: "say {backslash}" now"  # trailing\n')

        self.assertEqual('say " now', document["p"])

    def test_non_ascii_survives(self) -> None:
        document = yamlite.loads("prompt: por que no funciona la sesion\n")
        self.assertEqual(document["prompt"], "por que no funciona la sesion")

    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(yamlite.YamlError, "duplicate key"):
            yamlite.loads("id: a\nid: b\n")

    def test_tab_indentation_is_rejected(self) -> None:
        with self.assertRaisesRegex(yamlite.YamlError, "tab"):
            yamlite.loads("root:\n\tchild: 1\n")

    def test_unsupported_constructs_are_named_not_skipped(self) -> None:
        # Silently ignoring an anchor would drop a case, and a corpus that
        # loses cases overstates every rate computed from it.
        for text, expected in (
            ("a: &anchor 1\n", "anchors"),
            ("a: *alias\n", "aliases"),
            ("a: !!str 1\n", "tags"),
            ("a: |\n  x\n", "literal block"),
            ("a: [1, 2]\n", "flow collections"),
        ):
            with self.subTest(text=text):
                with self.assertRaises(yamlite.YamlError) as caught:
                    yamlite.loads(text)
                self.assertIn(expected, str(caught.exception))

    def test_empty_flow_collections_are_accepted(self) -> None:
        self.assertEqual(yamlite.loads("a: []\nb: {}\n"), {"a": [], "b": {}})

    def test_unterminated_quote_is_rejected(self) -> None:
        with self.assertRaisesRegex(yamlite.YamlError, "unterminated"):
            yamlite.loads('a: "open\n')


class CaseLoadingTests(TempTreeTestCase):
    def write(self, text: str) -> Path:
        return self.temp_file(text.lstrip("\n"), name="cases.yaml", prefix="cp-corpus-")

    def test_valid_corpus_loads_with_defaults_applied(self) -> None:
        cases = load_file(self.write(VALID), SKILLS)
        self.assertEqual(
            [case.case_id for case in cases], ["diagnose-click", "quiet-rename"]
        )
        self.assertTrue(cases[0].should_fire)
        self.assertFalse(cases[1].should_fire)
        self.assertEqual(cases[1].lang, "en")
        self.assertEqual(cases[1].mode, "all")
        self.assertFalse(cases[1].quick)

    def test_unknown_key_is_an_error(self) -> None:
        text = VALID.replace("    quick: true", "    quik: true")
        with self.assertRaisesRegex(CorpusError, "unknown keys quik"):
            load_file(self.write(text), SKILLS)

    def test_a_misspelled_expect_cannot_silently_invert_polarity(self) -> None:
        text = VALID.replace(
            "    expect:\n      - diagnose", "    expcet:\n      - diagnose"
        )
        with self.assertRaises(CorpusError):
            load_file(self.write(text), SKILLS)

    def test_unknown_workflow_name_is_rejected(self) -> None:
        text = VALID.replace("diagnose-systematically", "diagnose-thoroughly")
        with self.assertRaisesRegex(CorpusError, "unknown workflow"):
            load_file(self.write(text), SKILLS)

    def test_a_workflow_cannot_be_expected_and_forbidden(self) -> None:
        text = VALID.replace(
            "    forbid: []", "    forbid:\n      - diagnose-systematically"
        )
        with self.assertRaisesRegex(CorpusError, "both expected and forbidden"):
            load_file(self.write(text), SKILLS)

    def test_mode_any_needs_more_than_one_expectation(self) -> None:
        text = VALID.replace(
            "    fixture: webshop", "    fixture: webshop\n    mode: any"
        )
        with self.assertRaisesRegex(CorpusError, "at least two"):
            load_file(self.write(text), SKILLS)

    def test_wrong_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(CorpusError, "version"):
            load_file(self.write(VALID.replace("version: 1", "version: 2")), SKILLS)

    def test_duplicate_ids_across_files_are_rejected(self) -> None:
        directory = self.temp_dir("cp-corpus-dir-")
        (directory / "a.yaml").write_text(VALID.lstrip("\n"), encoding="utf-8")
        (directory / "b.yaml").write_text(VALID.lstrip("\n"), encoding="utf-8")
        with self.assertRaisesRegex(CorpusError, "duplicate case id"):
            load_corpus(directory, SKILLS)

    def test_an_empty_directory_is_an_error_not_an_empty_corpus(self) -> None:
        with self.assertRaisesRegex(CorpusError, "no case files"):
            load_corpus(self.temp_dir("cp-empty-"), SKILLS)


def case(**overrides) -> Case:
    values = {
        "case_id": "c",
        "prompt": "p",
        "lang": "en",
        "expect": ("diagnose-systematically",),
        "forbid": (),
        "fixture": "bare",
        "quick": False,
        "mode": "all",
        "source": "test",
    }
    values.update(overrides)
    return Case(**values)


class PassRuleTests(TempTreeTestCase):
    def test_should_fire_passes_only_on_the_named_workflow(self) -> None:
        subject = case()
        self.assertTrue(subject.satisfied_by(["diagnose-systematically"]))
        self.assertFalse(subject.satisfied_by(["refactor-cleanly"]))
        self.assertFalse(subject.satisfied_by([]))

    def test_should_not_fire_passes_only_on_silence(self) -> None:
        subject = case(expect=())
        self.assertTrue(subject.satisfied_by([]))
        self.assertFalse(subject.satisfied_by(["map-project"]))

    def test_forbid_fails_even_when_the_expectation_is_met(self) -> None:
        subject = case(
            expect=("diagnose-systematically",), forbid=("refactor-cleanly",)
        )
        self.assertFalse(
            subject.satisfied_by(["diagnose-systematically", "refactor-cleanly"])
        )

    def test_multi_skill_all_requires_every_named_workflow(self) -> None:
        subject = case(expect=("diagnose-systematically", "verify-delivery"))
        self.assertFalse(subject.satisfied_by(["diagnose-systematically"]))
        self.assertTrue(
            subject.satisfied_by(
                ["verify-delivery", "diagnose-systematically", "map-project"]
            )
        )

    def test_multi_skill_any_requires_one(self) -> None:
        subject = case(
            expect=("diagnose-systematically", "verify-delivery"), mode="any"
        )
        self.assertTrue(subject.satisfied_by(["verify-delivery"]))
        self.assertFalse(subject.satisfied_by([]))


class SelectionTests(TempTreeTestCase):
    def setUp(self) -> None:
        self.cases = [
            case(case_id="a", quick=True),
            case(case_id="b", expect=("refactor-cleanly",)),
            case(case_id="quiet", expect=(), quick=True),
        ]

    def test_a_skill_filter_keeps_the_negative_pool(self) -> None:
        # An activation rate reported without a false-positive rate beside it
        # is the number this corpus exists to keep company.
        chosen = select(self.cases, skills=["diagnose-systematically"])
        self.assertEqual([item.case_id for item in chosen], ["a", "quiet"])

    def test_quick_narrows_to_the_marked_cases(self) -> None:
        chosen = select(self.cases, quick=True)
        self.assertEqual([item.case_id for item in chosen], ["a", "quiet"])


class ShippedCorpusTests(TempTreeTestCase):
    """The corpus that actually ships, checked against the tree it measures."""

    def setUp(self) -> None:
        sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
        from skill_routing import load_skill_triggers

        self.installed = frozenset(load_skill_triggers(PLUGIN_ROOT))
        self.cases = load_corpus(PLUGIN_ROOT / "evals" / "cases", self.installed)

    def test_every_workflow_carries_at_least_three_should_fire_prompts(self) -> None:
        # A workflow measured by one prompt has a rate that is one prompt's
        # opinion. This is the seventh carrier a new workflow moves.
        sys.path.insert(0, str(PLUGIN_ROOT / "evals"))
        from run_activation_eval import MINIMUM_CASES_PER_SKILL, _under_covered

        self.assertEqual(_under_covered(self.cases, self.installed), [])
        self.assertGreaterEqual(MINIMUM_CASES_PER_SKILL, 3)

    def test_the_negative_pool_is_large_enough_to_bind(self) -> None:
        negatives = [case for case in self.cases if not case.should_fire]
        self.assertGreaterEqual(len(negatives), 15)
        # Near misses are the ones with a named sibling to stay out of; a pool
        # of pure off-domain trivia cannot fail in the way that matters.
        self.assertGreaterEqual(sum(1 for case in negatives if case.forbid), 8)

    def test_both_languages_are_represented_in_both_polarities(self) -> None:
        for polarity in ("should-fire", "should-not-fire"):
            for lang in ("en", "es"):
                with self.subTest(polarity=polarity, lang=lang):
                    self.assertTrue(
                        any(
                            case.polarity == polarity and case.lang == lang
                            for case in self.cases
                        )
                    )

    def test_the_quick_suite_covers_every_workflow(self) -> None:
        # The reduced suite is what a pull request runs. One that skipped a
        # workflow would let that workflow regress without any check failing,
        # which is the gap this whole mission exists to close.
        quick = select(self.cases, quick=True)
        covered = {name for case in quick for name in case.expect}
        self.assertEqual(sorted(self.installed - covered), [])
        self.assertTrue(any(not case.should_fire for case in quick))


if __name__ == "__main__":
    unittest.main()
