"""Run from the skill folder: python3 -m unittest discover -s scripts"""
import json
import random
import subprocess
import unittest
from collections import Counter

from crowd import (
    DEFAULT_DIMENSIONS, Persona, Reaction, balanced_column, build_command, make_personas,
    parse_cli_output, react, reaction_from_json, reaction_to_json, run_crowd, tally,
)

GOOD_ANSWER = {
    "first_impression": "some AI tool", "verdict": "ignore", "top_objection": "no price",
    "what_would_change_mind": "show the price", "price_reaction": "not shown", "quote": "meh",
}


def cli_stdout(answer=GOOD_ANSWER, is_error=False, cost=0.002):
    return json.dumps({"structured_output": answer, "is_error": is_error,
                       "result": "boom" if is_error else json.dumps(answer), "total_cost_usd": cost})


class PersonaTests(unittest.TestCase):
    def test_balanced_column_counts_differ_by_at_most_one(self):
        column = balanced_column(("a", "b", "c", "d"), 10, random.Random(1))
        counts = Counter(column).values()
        self.assertEqual(len(column), 10)
        self.assertLessEqual(max(counts) - min(counts), 1)

    def test_make_personas_is_deterministic_for_a_seed(self):
        self.assertEqual(make_personas(8, seed=3), make_personas(8, seed=3))
        self.assertNotEqual(make_personas(8, seed=3), make_personas(8, seed=4))

    def test_every_persona_has_every_dimension(self):
        for persona in make_personas(12, seed=7):
            self.assertEqual([k for k, _ in persona.traits], list(DEFAULT_DIMENSIONS))

    def test_custom_dimensions_replace_defaults(self):
        personas = make_personas(4, seed=1, dimensions={"activity": ("hiker", "climber")})
        self.assertEqual(Counter(dict(p.traits)["activity"] for p in personas), {"hiker": 2, "climber": 2})

    def test_rejects_out_of_range_n_and_empty_dimension(self):
        with self.assertRaises(ValueError):
            make_personas(0, seed=1)
        with self.assertRaises(ValueError):
            make_personas(61, seed=1)
        with self.assertRaises(ValueError):
            make_personas(3, seed=1, dimensions={"age": ()})


class CliTests(unittest.TestCase):
    def test_command_runs_isolated_with_schema(self):
        cmd = build_command("haiku")
        self.assertIn("--safe-mode", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("verdict", json.loads(cmd[cmd.index("--json-schema") + 1])["properties"])

    def test_parse_success(self):
        answer, error, cost = parse_cli_output(cli_stdout())
        self.assertEqual((answer["verdict"], error, cost), ("ignore", None, 0.002))

    def test_parse_error_payload_keeps_cost(self):
        answer, error, cost = parse_cli_output(cli_stdout(is_error=True, cost=0.01))
        self.assertIsNone(answer)
        self.assertIn("boom", error)
        self.assertEqual(cost, 0.01)

    def test_parse_rejects_non_json_and_bad_verdict(self):
        self.assertIsNone(parse_cli_output("Not logged in")[0])
        self.assertIsNone(parse_cli_output(cli_stdout(answer={**GOOD_ANSWER, "verdict": "love it"}))[0])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.persona = make_personas(1, seed=1)[0]

    def test_react_passes_persona_in_system_prompt_and_asset_in_stdin(self):
        seen = {}

        def runner(cmd, stdin):
            seen.update(system=cmd[cmd.index("--system-prompt") + 1], stdin=stdin)
            return 0, cli_stdout(), ""

        reaction = react(self.persona, "a", "PAGE TEXT", "Would you buy?", "haiku", runner)
        self.assertEqual(reaction.answer["verdict"], "ignore")
        self.assertIn(dict(self.persona.traits)["occupation"], seen["system"])
        self.assertIn("PAGE TEXT", seen["stdin"])
        self.assertIn("Would you buy?", seen["stdin"])

    def test_react_records_nonzero_exit_and_timeout(self):
        failed = react(self.persona, "a", "x", "q", "haiku", lambda c, s: (1, "", "auth expired"))
        self.assertIn("auth expired", failed.error)

        def timeout(cmd, stdin):
            raise subprocess.TimeoutExpired(cmd, 1)

        self.assertIn("TimeoutExpired", react(self.persona, "a", "x", "q", "haiku", timeout).error)

    def test_run_crowd_calls_once_per_persona_per_variant(self):
        calls = []

        def runner(cmd, stdin):
            calls.append(stdin)
            return 0, cli_stdout(), ""

        reactions = run_crowd(make_personas(3, seed=1), {"a": "A", "b": "B"}, "q", "haiku", runner=runner)
        self.assertEqual(len(calls), 6)
        self.assertEqual(Counter(r.variant for r in reactions), {"a": 3, "b": 3})


class TallyTests(unittest.TestCase):
    def reaction(self, pid, verdict, budget, variant="a"):
        traits = {"budget": budget, "disposition": "neutral"}
        return Reaction(pid, variant, traits, {**GOOD_ANSWER, "verdict": verdict}, cost_usd=0.5)

    def test_tally_shares_breakdown_and_failures(self):
        reactions = [self.reaction(0, "buy", "tight"), self.reaction(1, "ignore", "comfortable"),
                     self.reaction(2, "ignore", "tight"),
                     Reaction(3, "a", {}, None, "exit 1: auth", 0.0)]
        out = tally(reactions)
        self.assertIn("Calls: 4 · failed: 1 · cost: $1.500", out)
        self.assertIn("| ignore | 2/3 (67%) |", out)
        self.assertIn("| tight | 1 | 0 | 1 |", out)
        self.assertNotIn("### By disposition", out)  # single group, no breakdown
        self.assertIn("persona 3 (a): exit 1: auth", out)

    def test_tally_separates_variants(self):
        out = tally([self.reaction(0, "buy", "tight", "a"), self.reaction(0, "ignore", "tight", "b")])
        self.assertIn("## Variant: a", out)
        self.assertIn("## Variant: b", out)

    def test_jsonl_round_trip(self):
        original = self.reaction(5, "consider", "moderate")
        self.assertEqual(reaction_from_json(reaction_to_json(original)), original)


if __name__ == "__main__":
    unittest.main()
