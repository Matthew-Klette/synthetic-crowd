#!/usr/bin/env python3
"""Synthetic crowd: independent LLM personas react to a launch asset.

Each persona is one isolated headless `claude -p --safe-mode` call, so no
persona sees another's answer. Output is qualitative signal (objections,
confusion, price reaction), not a demand forecast.

  crowd.py personas --n 12 --seed 7 [--dims dims.json]       > personas.json
  crowd.py run --asset page.md [--asset variant_b.md] --question "..." \
               --personas personas.json --out results.jsonl [--model haiku]
  crowd.py tally results.jsonl                                > tally.md
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

DEFAULT_DIMENSIONS: Mapping[str, tuple[str, ...]] = {
    "age": ("18-24", "25-34", "35-44", "45-54", "55-64", "65+"),
    "occupation": (
        "student", "retail or service worker", "nurse", "software engineer",
        "small-business owner", "teacher", "tradesperson", "retiree",
        "marketing manager", "stay-at-home parent",
    ),
    "budget": ("tight: every purchase is weighed", "moderate", "comfortable"),
    "category_familiarity": (
        "never used anything like this", "casual user of similar products",
        "enthusiast who knows the alternatives", "burned before by a similar product",
    ),
    "disposition": ("skeptical of ads", "neutral", "early adopter"),
    "context": (
        "scrolling on a phone between tasks", "actively searching for a solution",
        "sent the link by a friend",
    ),
}

VERDICTS = ("buy", "consider", "ignore")
PRICE_REACTIONS = ("cheap", "fair", "expensive", "not shown")
MAX_PERSONAS = 60
DEFAULT_CONCURRENCY = 4
CALL_TIMEOUT_S = 180

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "first_impression": {"type": "string", "description": "What you think this is after 5 seconds"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "top_objection": {"type": "string"},
        "what_would_change_mind": {"type": "string"},
        "price_reaction": {"type": "string", "enum": list(PRICE_REACTIONS)},
        "quote": {"type": "string", "description": "One blunt sentence in your own voice"},
    },
    "required": ["first_impression", "verdict", "top_objection",
                 "what_would_change_mind", "price_reaction", "quote"],
    "additionalProperties": False,
}

SYSTEM_TEMPLATE = """You are a real person seeing this for the first time. Stay in character.

Who you are:
{traits}

How to answer:
- Most people ignore most launches and ads. Ignoring is the normal outcome; only say "buy" if you, specifically, would pay now.
- Nobody who made this will read your answer. Do not be polite, encouraging, or constructive for their sake.
- Judge only what is on the page. If something is unclear or missing, that counts against it.
- Speak from your own budget, job, and habits, not as a marketer or reviewer."""


@dataclass(frozen=True)
class Persona:
    pid: int
    traits: tuple[tuple[str, str], ...]

    def describe(self) -> str:
        return "\n".join(f"- {key.replace('_', ' ')}: {value}" for key, value in self.traits)

    def to_dict(self) -> dict:
        return {"pid": self.pid, "traits": dict(self.traits)}


@dataclass(frozen=True)
class Reaction:
    pid: int
    variant: str
    traits: Mapping[str, str]
    answer: Mapping[str, str] | None
    error: str | None = None
    cost_usd: float = 0.0


def balanced_column(values: Sequence[str], n: int, rng: random.Random) -> tuple[str, ...]:
    """n draws where each value's count differs from any other's by at most 1."""
    column: list[str] = []
    while len(column) < n:
        block = list(values)
        rng.shuffle(block)
        column.extend(block)
    return tuple(column[:n])


def make_personas(n: int, seed: int, dimensions: Mapping[str, Sequence[str]] = DEFAULT_DIMENSIONS) -> tuple[Persona, ...]:
    if not 1 <= n <= MAX_PERSONAS:
        raise ValueError(f"n must be 1..{MAX_PERSONAS}, got {n}")
    if not dimensions or any(not values for values in dimensions.values()):
        raise ValueError("every dimension needs at least one value")
    rng = random.Random(seed)
    columns = {key: balanced_column(tuple(values), n, rng) for key, values in dimensions.items()}
    return tuple(
        Persona(pid=i, traits=tuple((key, columns[key][i]) for key in dimensions))
        for i in range(n)
    )


def build_command(model: str) -> list[str]:
    return [
        "claude", "-p", "--safe-mode", "--model", model,
        "--no-session-persistence", "--tools", "",
        "--output-format", "json", "--json-schema", json.dumps(VERDICT_SCHEMA),
    ]


def build_user_message(asset: str, question: str) -> str:
    return f"Here is what you see:\n\n<asset>\n{asset}\n</asset>\n\nQuestion: {question}"


def parse_cli_output(stdout: str) -> tuple[dict | None, str | None, float]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None, f"non-JSON output: {stdout.strip()[:200]}", 0.0
    cost = float(payload.get("total_cost_usd") or 0.0)
    if payload.get("is_error"):
        return None, f"claude error: {str(payload.get('result'))[:200]}", cost
    answer = payload.get("structured_output")
    if not isinstance(answer, dict) or answer.get("verdict") not in VERDICTS:
        return None, f"missing or invalid structured_output: {str(answer)[:200]}", cost
    return answer, None, cost


Runner = Callable[[list[str], str], tuple[int, str, str]]


def subprocess_runner(cmd: list[str], stdin: str) -> tuple[int, str, str]:
    # Neutral cwd so the working path doesn't leak into persona context.
    with tempfile.TemporaryDirectory() as cwd:
        done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=cwd, timeout=CALL_TIMEOUT_S)
    return done.returncode, done.stdout, done.stderr


def react(persona: Persona, variant: str, asset: str, question: str, model: str, runner: Runner) -> Reaction:
    cmd = build_command(model) + ["--system-prompt", SYSTEM_TEMPLATE.format(traits=persona.describe())]
    traits = dict(persona.traits)
    try:
        code, stdout, stderr = runner(cmd, build_user_message(asset, question))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Reaction(persona.pid, variant, traits, None, f"{type(exc).__name__}: {exc}")
    if code != 0:
        return Reaction(persona.pid, variant, traits, None, f"exit {code}: {(stderr or stdout).strip()[-200:]}")
    answer, error, cost = parse_cli_output(stdout)
    return Reaction(persona.pid, variant, traits, answer, error, cost)


def run_crowd(personas: Sequence[Persona], variants: Mapping[str, str], question: str, model: str,
              concurrency: int = DEFAULT_CONCURRENCY, runner: Runner = subprocess_runner) -> tuple[Reaction, ...]:
    jobs = [(p, name, text) for name, text in variants.items() for p in personas]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(react, p, name, text, question, model, runner) for p, name, text in jobs]
        return tuple(f.result() for f in futures)


def _share(count: int, total: int) -> str:
    return f"{count}/{total} ({round(100 * count / total)}%)" if total else "0/0"


def tally(reactions: Sequence[Reaction], breakdown_dims: Sequence[str] = ("budget", "disposition")) -> str:
    lines = ["# Synthetic crowd tally", ""]
    failed = [r for r in reactions if r.answer is None]
    cost = sum(r.cost_usd for r in reactions)
    lines.append(f"Calls: {len(reactions)} · failed: {len(failed)} · cost: ${cost:.3f}")
    for variant in dict.fromkeys(r.variant for r in reactions):
        ok = [r for r in reactions if r.variant == variant and r.answer is not None]
        verdicts = Counter(r.answer["verdict"] for r in ok)
        prices = Counter(r.answer["price_reaction"] for r in ok)
        lines += ["", f"## Variant: {variant}", "",
                  "| verdict | share |", "|---|---|",
                  *(f"| {v} | {_share(verdicts[v], len(ok))} |" for v in VERDICTS),
                  "", "Price reaction: " + ", ".join(f"{p} {prices[p]}" for p in PRICE_REACTIONS)]
        for dim in breakdown_dims:
            groups = dict.fromkeys(r.traits.get(dim, "?") for r in ok)
            if len(groups) < 2:
                continue
            lines += ["", f"### By {dim.replace('_', ' ')}", "",
                      "| group | buy | consider | ignore |", "|---|---|---|---|"]
            for group in groups:
                counts = Counter(r.answer["verdict"] for r in ok if r.traits.get(dim, "?") == group)
                lines.append(f"| {group} | " + " | ".join(str(counts[v]) for v in VERDICTS) + " |")
        lines += ["", "### Objections (raw, cluster these)", ""]
        lines += [f"- [{r.answer['verdict']}] {r.answer['top_objection']} · fix: {r.answer['what_would_change_mind']}"
                  for r in ok]
        lines += ["", "### First impressions", ""]
        lines += [f"- {r.answer['first_impression']}" for r in ok]
    if failed:
        lines += ["", "## Failed calls", ""] + [f"- persona {r.pid} ({r.variant}): {r.error}" for r in failed]
    return "\n".join(lines) + "\n"


def reaction_to_json(r: Reaction) -> str:
    return json.dumps({"pid": r.pid, "variant": r.variant, "traits": dict(r.traits),
                       "answer": r.answer, "error": r.error, "cost_usd": r.cost_usd}, ensure_ascii=False)


def reaction_from_json(line: str) -> Reaction:
    d = json.loads(line)
    return Reaction(d["pid"], d["variant"], d["traits"], d.get("answer"), d.get("error"), d.get("cost_usd", 0.0))


def load_personas(path: Path) -> tuple[Persona, ...]:
    rows = json.loads(path.read_text())
    return tuple(Persona(row["pid"], tuple(row["traits"].items())) for row in rows)


def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_personas = sub.add_parser("personas")
    p_personas.add_argument("--n", type=int, default=12)
    p_personas.add_argument("--seed", type=int, default=7)
    p_personas.add_argument("--dims", type=Path, help="JSON {dimension: [values]} replacing the defaults")
    p_run = sub.add_parser("run")
    p_run.add_argument("--asset", type=Path, action="append", required=True, help="repeat for A/B variants")
    p_run.add_argument("--question", required=True)
    p_run.add_argument("--personas", type=Path, required=True)
    p_run.add_argument("--out", type=Path, required=True)
    p_run.add_argument("--model", default="haiku")
    p_run.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p_tally = sub.add_parser("tally")
    p_tally.add_argument("results", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "personas":
        dims = json.loads(args.dims.read_text()) if args.dims else DEFAULT_DIMENSIONS
        print(json.dumps([p.to_dict() for p in make_personas(args.n, args.seed, dims)], indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "run":
        variants = {path.stem: path.read_text() for path in args.asset}
        if len(variants) != len(args.asset):
            parser.error("variant files need distinct names")
        reactions = run_crowd(load_personas(args.personas), variants, args.question, args.model, args.concurrency)
        args.out.write_text("".join(reaction_to_json(r) + "\n" for r in reactions))
        failed = sum(r.answer is None for r in reactions)
        print(f"{len(reactions)} reactions → {args.out} ({failed} failed)", file=sys.stderr)
        return 1 if failed == len(reactions) else 0
    reactions = [reaction_from_json(line) for line in args.results.read_text().splitlines() if line.strip()]
    print(tally(reactions), end="")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
