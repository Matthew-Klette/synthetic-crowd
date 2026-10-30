# synthetic-crowd

A [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) skill that pre-tests a landing page, ad, email, price or launch message on a panel of AI customers before real people see it.

Each persona (different job, budget, market, attitude) is its own isolated headless `claude -p` call. No persona sees another's answer, so they can't drift toward one shared opinion. You get a buy / consider / ignore split, price reactions, a per-segment breakdown and the raw objections, which Claude then clusters into themes and turns into specific copy changes.

It finds objections and confusion. **It is not a conversion forecast.** LLM personas agree with each other more than real people do, so use it to compare versions (A vs B on the same panel), not to predict sales.

## How it works

1. **Asset**: the page, ad or offer as plain text, one file per variant.
2. **Panel**: `crowd.py personas` builds a balanced panel from audience dimensions (defaults describe general consumers; pass your own `dims.json` for a niche). A fixed seed rebuilds the same panel, so before/after comparisons are fair.
3. **Run**: one `claude -p --safe-mode --tools "" --json-schema …` call per persona per variant, from an empty temp folder: no CLAUDE.md, no tools, no MCP servers, no memory of other answers. The system prompt tells each persona that ignoring is the normal outcome and nobody who made the asset will read the answer, which counters LLM politeness.
4. **Answer**: every reply is forced into one JSON schema: `first_impression`, `verdict`, `top_objection`, `what_would_change_mind`, `price_reaction`, `quote`.
5. **Tally**: `crowd.py tally` counts verdicts, splits them by segment and lists every objection. Claude groups them into themes and proposes fixes.

## Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) installed and logged in (the `claude` command must work in your terminal)
- Python 3.9+ (standard library only, no packages to install)

Cost: a real run of 12 personas × 2 variants (24 calls) on Sonnet cost $0.33; Haiku, the default, is cheaper. Usage goes through your own Claude account.

## Install

Personal (all projects):

```bash
git clone https://github.com/Matthew-Klette/synthetic-crowd.git ~/.claude/skills/synthetic-crowd
```

Or for one project only, clone into `.claude/skills/synthetic-crowd` inside that project and update the script paths in `SKILL.md` to match.

Restart Claude Code, then ask something like:

> Test this landing page on 12 fake customers: is $49 too much?

> Compare these two headlines with a synthetic focus group.

## Use the script directly

```bash
python3 scripts/crowd.py personas --n 12 --seed 7 [--dims dims.json] > personas.json
python3 scripts/crowd.py run --asset a.md --asset b.md \
  --question "Would you buy this? Why or why not?" \
  --personas personas.json --out results.jsonl [--model haiku|sonnet]
python3 scripts/crowd.py tally results.jsonl > tally.md
```

## Tests

```bash
python3 -m unittest discover -s scripts
```

The tests mock the `claude` call, so they run offline and cost nothing.

## Privacy

The asset text and the persona prompt are sent to Anthropic through your own `claude` CLI, exactly like a normal Claude Code session. Nothing else leaves your machine, and the skill stores nothing outside the files you tell it to write.

## Licence

MIT. See [LICENSE](LICENSE).
