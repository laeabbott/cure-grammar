"""
Curate ~1000 high-quality sentences from the Tatoeba filtered set.

Selection logic:
- Fix false-positive causative_receptive tags (require させられ)
- Keep ALL genuine causative_receptive sentences (they're rare)
- Select a balanced, quality-weighted sample for other categories
- Prefer shorter sentences and those with cleaner particle use
- Deduplicate near-identical sentences (same structure, different names)

Output: data/curated_sentences.json
"""

import json
import re
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"

# ── Corrected causative_receptive detection ───────────────────────────────────

# させられ = clear causative-receptive (ichidan/する/す-godan)
TRUE_CR_RE = re.compile(r"させられ[るたてないよう]")

# Some reliable godan causative-receptive examples (苛立たせられる, etc.)
# Pattern: clearly a verb-a-stem + せられ where it can't be する-compound or potential
# For now, be conservative: only させられ
GODAN_CR_WHITELIST_RE = re.compile(
    r"[かきたなはまらわ]せられ[るたてないよう]"  # careful godan causative-receptive
)
# But exclude patterns that are almost certainly NOT causative-receptive:
# - [合わせられ/終わらせられ] (potential of causative)
# - [処せ/罰せ/課せ/科せ] (する-compound passive)
# - [魅せ/見せ/着せ/聞かせ] (receptive of compound causative-form verbs)
FALSE_CR_RE = re.compile(
    r"合わせられ|終わらせられ|済ませられ|間に合わせられ|やせられ"  # potential of causative
    r"|処せられ|罰せられ|課せられ|科せられ|列せられ|引き寄せられ|魅せられ|着せられ"
    r"|時間合わせられ|こなせられ"
)


def fix_grammar_points(entry: dict) -> dict:
    """Correct grammar_points based on more precise pattern matching."""
    text = entry["japanese"]
    gps = set(entry["grammar_points"])

    # Fix causative_receptive
    if "causative_receptive" in gps:
        is_true_cr = bool(TRUE_CR_RE.search(text))
        if not is_true_cr:
            # Check godan pattern with whitelist exclusions
            if GODAN_CR_WHITELIST_RE.search(text) and not FALSE_CR_RE.search(text):
                is_true_cr = True
        if not is_true_cr:
            gps.discard("causative_receptive")
            # Keep causative/receptive tags as appropriate

    entry["grammar_points"] = sorted(gps)
    return entry


# ── Quality scoring ───────────────────────────────────────────────────────────

JLPT_SCORE = {"N5": 5, "N4": 4, "N3": 3, "N2": 2, "N1": 1}
PENALTY_RE = re.compile(
    r"^(それ|これ|あれ|その|この|あの)は"  # starts with demonstrative topic (often too vague)
    r"|とのこと|というわけ"              # reportative or complex explanatory structure
    r"|[、。].*[、。].*[、。]"           # multiple clauses (too complex)
    r"|[ぁ-ん]{20,}"                    # very long kana-only run (unusual)
)
BONUS_RE = re.compile(
    r"に(よって|より|から)?(させ|れ|られ)"  # clear に-marked doer in receptive/causative
    r"|[をが].*[てで](あげ|くれ|もら)"      # clear object + giving verb
)


def score_entry(entry: dict) -> float:
    """Higher is better."""
    text = entry["japanese"]
    score = 0.0

    # Prefer shorter sentences (easier to drill)
    score += max(0, (50 - len(text)) / 10)

    # Prefer higher JLPT (simpler)
    score += JLPT_SCORE.get(entry["jlpt_level"], 2)

    # Penalty for complexity markers
    if PENALTY_RE.search(text):
        score -= 2

    # Bonus for clear, unambiguous structure
    if BONUS_RE.search(text):
        score += 1

    # Prefer sentences with Tatoeba JLPT tags (more reliable)
    if not entry.get("jlpt_estimated", True):
        score += 1

    # Prefer combination sentences (more drilling value per sentence)
    unique_cats = {
        gp for gp in entry["grammar_points"]
        if gp not in ("causative", "receptive")  # these appear as components of CR too
        or "causative_receptive" not in entry["grammar_points"]
    }
    score += len(unique_cats) * 0.5

    return score


# ── Target distribution ───────────────────────────────────────────────────────

# How many sentences per primary grammar point to target
TARGETS = {
    "causative_receptive": 999,  # keep all (rare)
    "causative": 120,
    "receptive": 220,
    "morau": 160,
    "kureru": 160,
    "ageru": 140,
}

# Minimum from combinations (e.g. receptive+kureru, causative+ageru)
COMBO_TARGET = 80


def primary_category(entry: dict) -> str:
    """Return the 'most interesting' grammar point for bucketing."""
    gps = set(entry["grammar_points"])
    if "causative_receptive" in gps:
        return "causative_receptive"
    # Combinations: prefer giving/receiving label
    giving = gps & {"morau", "kureru", "ageru"}
    struct = gps & {"causative", "receptive"}
    if giving:
        # Return the giving verb (morau > kureru > ageru)
        for g in ["morau", "kureru", "ageru"]:
            if g in giving:
                return g
    if "causative" in struct:
        return "causative"
    if "receptive" in struct:
        return "receptive"
    return list(gps)[0] if gps else "unknown"


def main():
    print("=== Sentence Curation ===\n")

    raw = json.load(open(DATA_DIR / "tatoeba_filtered.json", encoding="utf-8"))
    print(f"Starting with {len(raw):,} candidates")

    # Fix grammar points
    entries = [fix_grammar_points(e) for e in raw]

    # Remove entries that have no grammar points after correction
    entries = [e for e in entries if e["grammar_points"]]

    # Report corrected causative_receptive count
    cr_count = sum(1 for e in entries if "causative_receptive" in e["grammar_points"])
    print(f"After correction: {cr_count} true causative_receptive sentences")

    # Sort by score within each category
    for e in entries:
        e["_score"] = score_entry(e)
        e["_primary"] = primary_category(e)

    entries.sort(key=lambda e: -e["_score"])

    # Bucket by primary category
    buckets: dict[str, list] = defaultdict(list)
    for e in entries:
        buckets[e["_primary"]].append(e)

    print(f"\nAvailable per category:")
    for cat in ["causative_receptive", "causative", "receptive", "ageru", "kureru", "morau"]:
        print(f"  {cat:25s}: {len(buckets.get(cat, [])):,}")

    # Select sentences
    selected = []
    selected_ids = set()

    for cat, target in TARGETS.items():
        available = [e for e in buckets.get(cat, []) if e["id"] not in selected_ids]
        chosen = available[:target]
        selected.extend(chosen)
        for e in chosen:
            selected_ids.add(e["id"])
        print(f"  Selected {len(chosen):,} from {cat}")

    # Add more combination sentences if we haven't hit combo targets
    combo_added = 0
    for e in entries:
        if e["id"] in selected_ids:
            continue
        gps = set(e["grammar_points"])
        # Is it a genuine combination (e.g., receptive + kureru)?
        giving = gps & {"morau", "kureru", "ageru"}
        struct = gps & {"causative", "receptive"}
        if giving and struct and combo_added < COMBO_TARGET:
            selected.append(e)
            selected_ids.add(e["id"])
            combo_added += 1

    print(f"  + {combo_added} combination sentences")

    # Clean up temp fields
    for e in selected:
        e.pop("_score", None)
        e.pop("_primary", None)

    # Final sort: causative_receptive first, then by grammar_point count (combos), then JLPT
    jlpt_order = {"N5": 0, "N4": 1, "N3": 2, "N2": 3, "N1": 4}
    selected.sort(key=lambda e: (
        "causative_receptive" not in e["grammar_points"],
        -len(e["grammar_points"]),
        jlpt_order.get(e["jlpt_level"], 2),
        len(e["japanese"]),
    ))

    print(f"\nFinal selection: {len(selected):,} sentences")
    print("\nBreakdown:")
    cat_counts: dict[str, int] = defaultdict(int)
    for e in selected:
        for gp in e["grammar_points"]:
            cat_counts[gp] += 1
    for cat in ["causative_receptive", "causative", "receptive", "ageru", "kureru", "morau"]:
        print(f"  {cat:25s}: {cat_counts.get(cat, 0):,}")

    out_path = DATA_DIR / "curated_sentences.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(selected, f, ensure_ascii=False, indent=2)

    print(f"\nSaved → {out_path}")
    return out_path


if __name__ == "__main__":
    main()
