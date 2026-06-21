"""
Generate Cure Dolly-style glosses, breakdowns, and word maps for curated sentences.

Two modes:
  1. TEMPLATE mode (default): programmatic generation, no API needed
  2. API mode (--api-key KEY): uses Claude claude-haiku-4-5-20251001 for higher quality

Usage:
  python scripts/generate_content.py                    # template mode
  python scripts/generate_content.py --api-key sk-...  # API mode
  python scripts/generate_content.py --limit 50        # only process first 50

Output: data/sentences.json
"""

import json
import re
import argparse
import time
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"


# ── Verb stem extraction helpers ──────────────────────────────────────────────

# Map inflected forms back to dictionary form (rough)
# We'll extract the verb citation and stem from the Japanese pattern

PARTICLES = set("はがをにでとものへかやもよねぞぜわな")


def _strip_leading_particles(s: str) -> str:
    """Remove any leading particle characters from a stem string."""
    while s and s[0] in PARTICLES:
        s = s[1:]
    return s


def _last_match(pattern: str, text: str) -> str:
    """Return the last (rightmost) captured group from findall, stripped of leading particles."""
    matches = re.findall(pattern, text)
    if not matches:
        return ""
    stem = matches[-1] if isinstance(matches[-1], str) else matches[-1][0]
    return _strip_leading_particles(stem)


def extract_causative_verb(text: str) -> tuple[str, str]:
    """
    Extract (verb_stem, causative_form) for a causative sentence.
    Uses pure-kanji anchoring: only kanji characters are captured before させ/せ,
    preventing hiragana particles from being included.
    """
    # させ — capture 1-4 KANJI before させ: e.g. 退学させ, 告白させ, 直させ
    stem = _last_match(r"([一-龯]{1,4})させ[るたてないよう]", text)
    if stem:
        return stem, stem + "させる"

    # Godan a-row: [kanji]+[a-row hiragana]+せ: e.g. 書かせ, 飲ませ, 歩かせ
    stem = _last_match(r"([一-龯]{1,3}[かさたなはまやらわがざだばぱ])せ[るたてないよう]", text)
    if stem:
        return stem, stem + "せる"

    # Hiragana-only verb stems (rare but e.g. いさせ for 居させ)
    stem = _last_match(r"([ぁ-ん]{1,2})させ[るたてないよう]", text)
    if stem:
        return stem, stem + "させる"

    return "", ""


def extract_receptive_verb(text: str) -> tuple[str, str]:
    """
    Extract (verb_stem, receptive_form) for a receptive sentence.
    Pure-kanji anchoring prevents particle capture.
    """
    # Ichidan (and CR) られ: e.g. 食べられ, 見られ, させられ
    stem = _last_match(r"([一-龯]{1,4})られ[るたてないよう]", text)
    if stem and stem not in ("させ",):
        return stem, stem + "られる"

    # Godan a-row れ: e.g. 頼まれ, 叱られ→叱ら+れ, 書かれ, 掴まれ
    stem = _last_match(r"([一-龯]{1,3}[かさたなはまやらわがざだばぱ])れ[るたてないよう]", text)
    if stem:
        return stem, stem + "れる"

    # Causative-receptive: the base before させ
    stem = _last_match(r"([一-龯]{1,4})させられ", text)
    if stem:
        return stem, stem + "させられる"

    return "", ""


def extract_te_verb(text: str, helper: str) -> str:
    """Extract the verb before a て-form + helper."""
    # Find てHELPER or でHELPER
    m = re.search(r"(\S+)[てで]" + re.escape(helper[0]), text)
    if m:
        return m.group(1)
    return ""


# ── Gloss generators ──────────────────────────────────────────────────────────

def make_gloss_receptive(jp: str, en: str) -> str:
    """Generate Cure Dolly gloss for receptive sentences."""
    # Try to identify the subject (が-marked) and agent (に-marked)
    subj_m = re.search(r"(\S+)が", jp)
    agent_m = re.search(r"(\S+)に", jp)

    subj = subj_m.group(1) if subj_m else "∅"
    agent = f"[by {agent_m.group(1)}]" if agent_m else ""

    # Strip Japanese-specific words for gloss subject
    # Use English translation as semantic base, restructure structurally
    # Pattern: "[Subject] got [action] [by agent]"
    en_clean = en.rstrip(".?!")

    # If en has passive "was/were Xed", convert to "got X-ed"
    en_struct = re.sub(r"\b(was|were|is|are|has been|have been) ", "got-", en_clean, count=1)

    return f"[structural]: {en_struct.strip()}"


def make_template_gloss(entry: dict) -> str:
    """Generate a Cure Dolly-style structural gloss from grammar points + English."""
    jp = entry["japanese"]
    en = entry["natural_english"]
    gps = set(entry["grammar_points"])

    en_clean = en.rstrip(".?!")

    if "causative_receptive" in gps:
        # "[Subject] received being-caused-to-[verb]"
        # Transform English: "I was made to eat X" → "I received being-caused-to-eat X"
        en_struct = re.sub(
            r"\b(was|were) (made|forced|compelled) to (\w+)",
            r"received being-caused-to-\3",
            en_clean
        )
        en_struct = re.sub(
            r"\b(made|forced|compelled) (me|him|her|us|them) to (\w+)",
            r"caused \2 to \3 → \2 received that causing",
            en_struct
        )
        if en_struct == en_clean:
            en_struct = f"[received being-caused-to-...] ← {en_clean}"
        return en_struct

    if "causative" in gps and "receptive" not in gps:
        # "caused [target] to [verb]"
        en_struct = re.sub(
            r"\b(made|let|forced|allowed|had) (someone|\w+) (to )?(\w+)",
            r"caused \2 to \4",
            en_clean
        )
        # For compound causatives like 知らせる (cause-to-know = tell)
        return en_struct if en_struct != en_clean else f"[caused to...] ← {en_clean}"

    if "receptive" in gps:
        en_struct = re.sub(
            r"\b(was|were|is|are|has been|have been) (\w+ed\b)",
            r"got \2",
            en_clean
        )
        return en_struct if en_struct != en_clean else f"[got...] ← {en_clean}"

    if "morau" in gps:
        en_struct = re.sub(
            r"\b(I|we) (got|had|asked) (\w+) to (\w+)",
            r"I received [\3 doing \4]",
            en_clean
        )
        return f"[received the action of...] ← {en_clean}" if en_struct == en_clean else en_struct

    if "kureru" in gps:
        return f"[gave-down to me: the act of...] ← {en_clean}"

    if "ageru" in gps:
        return f"[gave-up to other: the act of...] ← {en_clean}"

    return en_clean


def _en_to_got(en: str) -> str:
    """Transform English passive/perfect to 'got' form for Cure Dolly structural gloss."""
    s = en.rstrip(".?!")
    s = re.sub(r"\b(was|were) (being )?(\w+(?:ed|en|t)\b)", r"got \3", s)
    s = re.sub(r"\b(is|are) (\w+(?:ed|en|t)\b)", r"gets \2", s)
    # "has/have been Xed" before "has/have X" so the specific case wins
    s = re.sub(r"\b(has|have) been (\w+(?:ed|en)\b)", r"got \2", s)
    # "has/have X" for intransitive perfects: "has lifted" → "got lifted"
    s = re.sub(r"\b(has|have) (\w+(?:ed|en|t)\b)", r"got \2", s)
    return s


def make_cure_dolly_gloss(entry: dict) -> str:
    """
    Generate a Cure Dolly-style structural English gloss.
    Primarily transforms the natural English translation to show logical structure.
    - receptive: 'was Xed' → 'got X-ed'
    - causative: 'made/let/had Y do X' → 'caused Y to X'
    - causative-receptive: 'was made to X' → 'got made to X' / 'received being-caused-to-X'
    - kureru: add 'gave-down-to-me' framing
    - ageru: add 'gave-up' framing
    - morau: 'got X done' → 'received [someone] X-ing'
    """
    en = entry["natural_english"].rstrip(".?!")
    gps = set(entry["grammar_points"])

    if "causative_receptive" in gps:
        # "was made/forced/compelled to X" → "got made to X"
        s = re.sub(r"\b(was|were) (made|forced|compelled|required) to (\w+)", r"got made to \3", en)
        # "made/forced me/him/her to X" → "got caused to X"
        s = re.sub(r"\b(made|forced|compelled) (me|him|her|us|them) to (\w+)", r"got caused to \3", s)
        if s == en:
            # fallback: just show the received-being-caused structure
            s = f"[received being-caused-to-...] — {en}"
        return s

    if "causative" in gps and "receptive" not in gps and "morau" not in gps:
        # "made/let/had Y X" → "caused Y to X"
        s = re.sub(r"\b(made|let|allowed|had|forced) (\w+) (to )?(\w+)", r"caused \2 to \4", en)
        # "will let X know" → "will cause X to know"
        s = re.sub(r"\blet (\w+) know\b", r"cause \1 to know", s)
        # "I'll tell you" → "cause you to hear" — skip, keep as is
        if s == en:
            s = f"[caused-to-...] — {en}"
        return s

    if "receptive" in gps:
        s = _en_to_got(en)
        # If transformation didn't help, flag structurally
        if s == en:
            s = f"[got ...] — {en}"
        return s

    if "morau" in gps:
        # "I got X done", "I had X done", "get examined by doctor"
        s = re.sub(r"\b(I|we) (got|had|asked) (\w+) to (\w+)", r"I received [\3 \4-ing for me]", en)
        s = re.sub(r"\b(get|have) (examined|checked|cut|done|looked at)", r"receive being \2", s)
        if "causative" in gps:
            # させてもらう — "receive permission to do X"
            s = re.sub(
                r"\b(I'?l?l?|I will) (\w+)\b",
                r"I receive being-caused-to-\2",
                en, count=1
            )
        if s == en:
            s = f"[received the action of...] — {en}"
        return s

    if "kureru" in gps:
        # Add "gave-down-to-me" framing after the action verb
        s = re.sub(r"\b(didn'?t?|won'?t) (\w+)(.*)", r"didn't give me the \2-ing\3", en)
        if s == en:
            s = re.sub(r"\b(\w+ed)\b", r"\1 [giving-down-to-me]", en, count=1)
        if s == en:
            s = f"[gave-down-to-me:] — {en}"
        return s

    if "ageru" in gps:
        s = re.sub(r"\b(I|we) (\w+)(.*) (for|to) (you|him|her|them)\b", r"I gave-up: \2-ing\3 to \5", en)
        if s == en:
            s = f"[gave-up to other:] — {en}"
        return s

    return en


# ── Breakdown generators ──────────────────────────────────────────────────────

BREAKDOWN_TEMPLATES = {
    "causative_receptive": (
        "Three verbs, two actors: [{base_verb}] is the action performed, "
        "[させる] is the causing (done by someone else), "
        "[られる] is the receiving (done by the subject). "
        "The subject receives being-caused-to-{base_verb_en}. "
        "In Cure Dolly's framing: the main engine of this sentence is られる (receive/get). "
        "The causing (させる) belongs to the other party. The subject and the doer of "
        "the base action are always the same person."
    ),
    "causative": (
        "The causative helper [{caus_form}] attaches to the あ-stem of [{base_verb}]. "
        "Two verbs, two actors: the subject causes; the target performs the action. "
        "Cure Dolly's key insight: せる/させる means 'cause by any means' — "
        "not necessarily 'force' or 'allow', but cause: set up conditions under which the action occurs."
    ),
    "receptive": (
        "The receptive helper [{recep_form}] attaches to the あ-stem of [{base_verb}]. "
        "The subject (が-marked) receives the action. The doer (if mentioned) is marked with に, "
        "the source of the pull. The core engine of this sentence is れる/られる: 'get'."
    ),
    "morau": (
        "もらう is a pull-receive verb — the receiver takes initiative. "
        "てもらう: the subject received/pulled the action of [{te_verb}] from the source (に-marked). "
        "Cure Dolly compares this directly to れる/られる: both are receptive (pull) verbs. "
        "Unlike くれる (where the giver takes initiative), here the receiver is in control."
    ),
    "kureru": (
        "くれる means 'give downward' — someone gives to me or my sphere. "
        "てくれる: [{actor}] gives me the action of [{te_verb}]. "
        "The giver took the initiative. 'Downward' is not literally about height — "
        "it reflects the Japanese convention of placing oneself lower than others. "
        "If the action is given to me, it's くれる; if I give to others, it's あげる."
    ),
    "ageru": (
        "あげる means 'give upward' — giving to someone outside my sphere. "
        "てあげる: [{actor}] gives someone outside their sphere the action of [{te_verb}]. "
        "Note: using あげる about actions done for superiors can sound presumptuous "
        "(as if you're doing them a favor) — it's not keigo."
    ),
}


def make_breakdown(entry: dict) -> str:
    """Generate Cure Dolly-style answer breakdown."""
    jp = entry["japanese"]
    en = entry["natural_english"]
    gps = set(entry["grammar_points"])

    # Extract verb forms
    caus_stem, caus_form = extract_causative_verb(jp)
    recep_stem, recep_form = extract_receptive_verb(jp)

    base_verb = caus_stem or recep_stem or "..."
    base_verb_en = re.sub(r"させ(る|た|て|ない)$", "", caus_form or recep_form or "")

    # Actor extraction
    subj_m = re.search(r"([^\s。、！？]+)が", jp)
    topic_m = re.search(r"([^\s。、！？]+)は", jp)
    actor = (subj_m.group(1) if subj_m else "") or (topic_m.group(1) if topic_m else "the subject")

    # Te-form verb for giving/receiving — kanji-anchored to prevent particle capture
    te_verb = ""
    for helper in ["くれ", "あげ", "もら"]:
        pre_m = re.search(r"([一-龯]{1,3}[ぁ-ん]{0,2})[てで]" + helper, jp)
        if pre_m:
            te_verb = pre_m.group(1)
            break

    # Pick primary breakdown template
    priority = ["causative_receptive", "causative", "receptive", "morau", "kureru", "ageru"]
    for gp in priority:
        if gp in gps:
            template = BREAKDOWN_TEMPLATES.get(gp, "")
            breakdown = template.format(
                base_verb=base_verb or "...",
                base_verb_en=base_verb_en or "do",
                caus_form=caus_form or "させる",
                recep_form=recep_form or "られる",
                actor=actor,
                te_verb=te_verb or base_verb or "...",
            )
            # Add combo note if sentence has multiple grammar points
            combos = [g for g in gps if g != gp and g not in ("causative", "receptive")]
            if combos:
                combo_str = " + ".join(combos)
                breakdown += f"\n\nThis sentence also uses: {combo_str}."
            return breakdown

    return f"This sentence uses: {', '.join(sorted(gps))}."


# ── Word map generator ────────────────────────────────────────────────────────

def make_word_map(entry: dict) -> list[dict]:
    """
    Generate word-level English→Japanese mapping.
    Basic heuristic: split English by words and try to identify
    Japanese equivalents from known patterns.
    """
    jp = entry["japanese"]
    en = entry["natural_english"]
    gps = set(entry["grammar_points"])
    word_map = []

    # Map grammar-point-specific constructions first
    if "causative_receptive" in gps:
        m = re.search(r"させられ[るたてないよう]", jp)
        if m:
            word_map.append({
                "english": "was made to / was forced to / got caused to",
                "japanese": "させられ" + m.group(0)[-1],
                "note": "causative-receptive"
            })

    elif "causative" in gps:
        m = re.search(r"させ[るたてないよう]|[かさたなはまやらわがざだばぱ]せ[るたてないよう]", jp)
        if m:
            word_map.append({
                "english": "make/cause/let [someone] do",
                "japanese": m.group(0),
                "note": "causative helper"
            })

    if "receptive" in gps and "causative_receptive" not in gps:
        m = re.search(r"られ[るたてないよう]|[かさたなはまやらわがざだばぱ]れ[るたてないよう]", jp)
        if m:
            word_map.append({
                "english": "got / received [the action]",
                "japanese": m.group(0),
                "note": "receptive helper"
            })

    if "morau" in gps:
        m = re.search(r"[てで]もら[うえっいよ]|[てで]貰[うえっいよ]", jp)
        if m:
            word_map.append({
                "english": "received [someone doing this for me]",
                "japanese": m.group(0),
                "note": "て+もらう"
            })

    if "kureru" in gps:
        m = re.search(r"[てで]くれ[るたてないよう]|[てで]くれな[かいっ]", jp)
        if m:
            word_map.append({
                "english": "gave [this action] to me",
                "japanese": m.group(0),
                "note": "て+くれる"
            })

    if "ageru" in gps:
        m = re.search(r"[てで]あげ[るたてないよう]|[てで]上げ[るたてないよう]", jp)
        if m:
            word_map.append({
                "english": "gave [this action] to [someone]",
                "japanese": m.group(0),
                "note": "て+あげる"
            })

    # Add particle markers
    agent_m = re.search(r"([^\s。、！？]+)に", jp)
    if agent_m:
        agent_word = agent_m.group(1)
        if "receptive" in gps:
            word_map.append({
                "english": "by [agent] / from [agent]",
                "japanese": agent_word + "に",
                "note": "に marks the source of the pull-action"
            })
        elif "causative" in gps:
            word_map.append({
                "english": "[target of causing]",
                "japanese": agent_word + "に",
                "note": "に marks target when を is already taken"
            })

    obj_m = re.search(r"([^\s。、！？]+)を", jp)
    if obj_m:
        word_map.append({
            "english": "[direct object]",
            "japanese": obj_m.group(1) + "を",
            "note": "を marks object"
        })

    return word_map


# ── API mode ──────────────────────────────────────────────────────────────────

API_SYSTEM_PROMPT = """You are a Japanese grammar expert specializing in the Cure Dolly framework.

Cure Dolly's key concepts:
- RECEPTIVE (受身): れる/られる is a RECEIVE helper verb, NOT passive voice. The subject GETS/RECEIVES the action. Doer is に-marked (source of pull). Core engine = れる/られる.
- CAUSATIVE: せる/させる means CAUSE (by any means: force, allow, or set conditions). Two verbs, two actors always.
- CAUSATIVE-RECEPTIVE: Three verbs (base+させる+られる), two actors. Subject receives being-caused-to-do. Same person who received is same who did the base action.
- くれる: give downward → someone gives action TO ME/MY SPHERE
- あげる: give upward → I/we give action TO OTHERS
- もらう: pull-receive, receiver takes initiative. Like れる/られる but active-pull not passive-receive.

Generate structural glosses that show the logical skeleton — not the natural English translation, but a "structural English" that mirrors the Japanese sentence structure, using: "got X-ed" (receptive), "caused to X" (causative), "received being-caused-to-X" (causative-receptive), "gave-down the action of X" (kureru), "gave-up the action of X" (ageru), "received [someone] X-ing" (morau)."""

API_USER_TEMPLATE = """Given this Japanese sentence and its natural English translation, generate:
1. A Cure Dolly-style structural English gloss (1 sentence showing logical structure, not natural English)
2. A Cure Dolly-framework breakdown for learners (2-4 sentences explaining WHY this construction works as it does, referencing specific concepts like あ-stem, two-actors rule, pull-vs-push, etc.)
3. Key word mappings (3-6 entries mapping significant English phrases to their Japanese equivalents)

Japanese: {japanese}
Natural English: {natural_english}
Grammar points: {grammar_points}

Respond in JSON:
{{
  "cure_dolly_gloss": "...",
  "answer_breakdown": "...",
  "word_map": [
    {{"english": "...", "japanese": "...", "note": "..."}}
  ]
}}"""


def generate_via_api(entries: list[dict], api_key: str, limit: int = None) -> list[dict]:
    """Use Claude API to generate high-quality content."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    results = []
    to_process = entries[:limit] if limit else entries

    for i, entry in enumerate(to_process):
        print(f"  [{i+1}/{len(to_process)}] {entry['japanese'][:30]}...")
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                system=API_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": API_USER_TEMPLATE.format(
                        japanese=entry["japanese"],
                        natural_english=entry["natural_english"],
                        grammar_points=", ".join(entry["grammar_points"]),
                    )
                }]
            )
            text = resp.content[0].text.strip()
            # Strip markdown code fences if present
            text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
            parsed = json.loads(text)
            entry = dict(entry)
            entry["cure_dolly_gloss"] = parsed.get("cure_dolly_gloss", "")
            entry["answer_breakdown"] = parsed.get("answer_breakdown", "")
            entry["word_map"] = parsed.get("word_map", [])
            results.append(entry)
            time.sleep(0.3)  # be polite to rate limits
        except Exception as e:
            print(f"    ERROR: {e} — falling back to template")
            entry = dict(entry)
            entry["cure_dolly_gloss"] = make_cure_dolly_gloss(entry)
            entry["answer_breakdown"] = make_breakdown(entry)
            entry["word_map"] = make_word_map(entry)
            results.append(entry)

    # For any unprocessed entries (if limit was set), use templates
    if limit and limit < len(entries):
        for entry in entries[limit:]:
            e = dict(entry)
            e["cure_dolly_gloss"] = make_cure_dolly_gloss(e)
            e["answer_breakdown"] = make_breakdown(e)
            e["word_map"] = make_word_map(e)
            results.append(e)

    return results


def generate_via_templates(entries: list[dict]) -> list[dict]:
    """Generate content using templates (no API needed)."""
    results = []
    for entry in entries:
        e = dict(entry)
        e["cure_dolly_gloss"] = make_cure_dolly_gloss(e)
        e["answer_breakdown"] = make_breakdown(e)
        e["word_map"] = make_word_map(e)
        results.append(e)
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="Anthropic API key for high-quality generation")
    parser.add_argument("--limit", type=int, help="Process only first N sentences via API")
    parser.add_argument("--input", default=str(DATA_DIR / "curated_sentences.json"))
    parser.add_argument("--output", default=str(DATA_DIR / "sentences.json"))
    args = parser.parse_args()

    print("=== Content Generation ===\n")

    entries = json.load(open(args.input, encoding="utf-8"))
    print(f"Processing {len(entries)} sentences...")

    if args.api_key:
        print(f"Mode: Claude API (haiku)")
        print(f"API limit: {args.limit or 'all'}")
        results = generate_via_api(entries, args.api_key, limit=args.limit)
    else:
        print("Mode: Template-based (no API key provided)")
        print("Tip: run with --api-key sk-... for higher-quality glosses")
        results = generate_via_templates(entries)

    # Report stats
    with_gloss = sum(1 for e in results if e.get("cure_dolly_gloss"))
    with_breakdown = sum(1 for e in results if e.get("answer_breakdown"))
    print(f"\nResults:")
    print(f"  cure_dolly_gloss: {with_gloss}/{len(results)}")
    print(f"  answer_breakdown: {with_breakdown}/{len(results)}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nSaved → {args.output}")

    # Show 3 sample outputs
    print("\n=== Sample outputs ===")
    samples = [e for e in results if "causative_receptive" in e["grammar_points"]][:2]
    samples += [e for e in results if "kureru" in e["grammar_points"]][:1]
    for s in samples:
        print(f"\nJP: {s['japanese']}")
        print(f"EN: {s['natural_english']}")
        print(f"Gloss: {s['cure_dolly_gloss']}")
        print(f"Breakdown: {s['answer_breakdown'][:120]}...")
        print(f"Grammar: {s['grammar_points']}")


if __name__ == "__main__":
    main()
