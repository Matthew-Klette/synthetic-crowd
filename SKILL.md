---
name: synthetic-crowd
description: Pre-test a landing page, ad, email, price or launch message on a panel of independent LLM personas (different ages, jobs, budgets, attitudes) before it goes live. Each persona is a separate headless Claude call, so nobody sees anyone else's answer. Returns a verdict split (buy / consider / ignore), price reaction, per-segment breakdown, and the raw objections to cluster. Use when someone wants to "test this on fake customers", "run a synthetic focus group", "will people love this or ignore it", compare two variants of copy or creative before launch, or mentions MiroFish / AI focus groups. It finds objections and confusion; it is NOT a conversion forecast.
---

# Synthetic Crowd

A cheap way to get first reactions to a launch asset before real traffic sees it. It's not a forecast.

**Good at:** confusion ("what is this?"), missing information (no price, no proof), objections you didn't think of, and **A vs B comparisons** on the same panel.
**Bad at:** absolute numbers. "40% would buy" means nothing. LLM personas agree with each other more than real people do, and they shift with how the prompt is worded. Use real data (GA4, Clarity, an A/B test) for anything you'd stake money on.

## Procedure

1. **Get the asset as text.** For a URL, fetch the page copy (any scraper or browser tool you have) and save what a visitor actually sees to `<variant>.md`: headline, subhead, price, CTA, proof. For an ad, write the caption plus a one-line description of the visual. Each A/B variant gets its own file, and the file stem becomes the variant name.
2. **Define the audience. Don't skip this.** The default dimensions describe general consumers. For anything aimed at a niche (B2B, founders, outdoor gear buyers), write `dims.json` with that audience's real dimensions, otherwise half the panel says "this isn't for me" and the run is wasted. Example for an outdoor apparel brand:
   ```json
   {"age": ["25-34","35-44","45-54","55-64"],
    "activity": ["weekend hiker","climber","travels light, city + trail","buys outdoor brands for everyday wear"],
    "budget": ["deal hunter","pays for quality","price no object"],
    "brand_relationship": ["never heard of the brand","owns one piece","loyal to a competitor"],
    "context": ["Instagram ad mid-scroll","Google search for hiking pants","abandoned cart email"]}
   ```
3. **Generate the panel:** `python3 ~/.claude/skills/synthetic-crowd/scripts/crowd.py personas --n 12 --seed 7 --dims dims.json > personas.json`. The panel is balanced: each value appears roughly equally often. The same seed gives the same panel, so keep the seed fixed when comparing versions.
4. **Run it:** `python3 …/crowd.py run --asset a.md [--asset b.md] --question "<one question>" --personas personas.json --out results.jsonl`. Ask a single, plain question ("Would you click? Why or why not?"). 12 personas × 2 variants = 24 headless calls (a real 24-call Sonnet run cost $0.33; Haiku is cheaper), run 4 at a time. Use `--model sonnet` only when the asset needs domain judgement.
5. **Tally:** `python3 …/crowd.py tally results.jsonl > tally.md`.
6. **Interpret** (your job, not the script's):
   - Group the raw objections into 3–5 themes and count each theme. Quote the bluntest line for each.
   - Read the first impressions. If most personas can't say what it is, that's the main finding.
   - With A/B, report the **difference** between variants on the same panel, never either absolute share alone.
   - End with 1–3 specific copy or creative changes, each tied to an objection theme.
   - If more than a third of the panel says "not for me", the audience dimensions were wrong. Fix `dims.json` and rerun before drawing conclusions.

## Rules

- Isolation is the method. Every persona runs in its own `claude -p --safe-mode --tools ""` call from a neutral temp dir: no CLAUDE.md, no tools, no MCP, and no persona sees another's answer. Never batch personas into one prompt, because they converge on the same answer.
- The system prompt counters politeness ("ignoring is the normal outcome; nobody who made this will read your answer"). Don't soften it.
- Use 8–20 personas. More doesn't add new objections, it repeats them. The script caps at 60.
- Failed calls are listed in the tally, never dropped silently. If everything fails, it's usually `claude` being logged out: run `claude` → `/login`.
- The asset is sent to Anthropic through the `claude` CLI, the same route as any Claude Code session. Don't run it on material your data policy keeps off third-party services.

## When to use MiroFish instead

[MiroFish](https://github.com/666ghj/MiroFish) (AGPL-3.0, built on CAMEL-AI's OASIS) simulates agents **interacting**: they post, reply and spread opinions on simulated social platforms, seeded from documents you upload, and it ends in a report. Use it when you're asking how an opinion spreads (a PR crisis, a policy reaction), not whether someone would click. It needs Docker or Node+Python, an OpenAI-compatible LLM key (the README warns usage is "high consumption"), and a Zep Cloud key. It isn't free to run.

## After you change the asset

Re-run on the same `personas.json` (same seed) so before and after are judged by the same panel. That comparison is the most reliable thing this skill produces.
