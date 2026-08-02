"""The corpus half of the activation eval: the YAML subset and the case rules."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "evals") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "evals"))

from activation_core import yamlite  # noqa: E402
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


def write(text: str) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="cp-corpus-"))
    path = directory / "cases.yaml"
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    return path


class YamliteTests(unittest.TestCase):
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


class CaseLoadingTests(unittest.TestCase):
    def test_valid_corpus_loads_with_defaults_applied(self) -> None:
        cases = load_file(write(VALID), SKILLS)
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
            load_file(write(text), SKILLS)

    def test_a_misspelled_expect_cannot_silently_invert_polarity(self) -> None:
        text = VALID.replace(
            "    expect:\n      - diagnose", "    expcet:\n      - diagnose"
        )
        with self.assertRaises(CorpusError):
            load_file(write(text), SKILLS)

    def test_unknown_workflow_name_is_rejected(self) -> None:
        text = VALID.replace("diagnose-systematically", "diagnose-thoroughly")
        with self.assertRaisesRegex(CorpusError, "unknown workflow"):
            load_file(write(text), SKILLS)

    def test_a_workflow_cannot_be_expected_and_forbidden(self) -> None:
        text = VALID.replace(
            "    forbid: []", "    forbid:\n      - diagnose-systematically"
        )
        with self.assertRaisesRegex(CorpusError, "both expected and forbidden"):
            load_file(write(text), SKILLS)

    def test_mode_any_needs_more_than_one_expectation(self) -> None:
        text = VALID.replace(
            "    fixture: webshop", "    fixture: webshop\n    mode: any"
        )
        with self.assertRaisesRegex(CorpusError, "at least two"):
            load_file(write(text), SKILLS)

    def test_wrong_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(CorpusError, "version"):
            load_file(write(VALID.replace("version: 1", "version: 2")), SKILLS)

    def test_duplicate_ids_across_files_are_rejected(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="cp-corpus-dir-"))
        (directory / "a.yaml").write_text(VALID.lstrip("\n"), encoding="utf-8")
        (directory / "b.yaml").write_text(VALID.lstrip("\n"), encoding="utf-8")
        with self.assertRaisesRegex(CorpusError, "duplicate case id"):
            load_corpus(directory, SKILLS)

    def test_an_empty_directory_is_an_error_not_an_empty_corpus(self) -> None:
        with self.assertRaisesRegex(CorpusError, "no case files"):
            load_corpus(Path(tempfile.mkdtemp(prefix="cp-empty-")), SKILLS)


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


class PassRuleTests(unittest.TestCase):
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


class SelectionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
