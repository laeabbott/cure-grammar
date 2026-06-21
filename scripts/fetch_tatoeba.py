"""
Fetch and filter Japanese sentences from Tatoeba for the Cure Grammar drill app.

Tatoeba exports (all bz2-compressed TSVs):
  jpn_sentences.tsv   — id, lang, text     (per-language Japanese sentences)
  eng_sentences.tsv   — id, lang, text     (per-language English sentences)
  links.tsv           — sentence_id, translation_id  (cross-language links)
  jpn_tags.tsv        — sentence_id, tag   (tags including JLPT)

Usage:
  python scripts/fetch_tatoeba.py

Outputs:
  data/tatoeba_filtered.json
"""

import re
import json
import urllib.request
import bz2
import shutil
import tarfile
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

BASE_URL = "https://downloads.tatoeba.org/exports"

FILES = {
    "jpn_sentences": f"{BASE_URL}/per_language/jpn/jpn_sentences.tsv.bz2",
    "eng_sentences": f"{BASE_URL}/per_language/eng/eng_sentences.tsv.bz2",
    "jpn_tags": f"{BASE_URL}/per_language/jpn/jpn_tags.tsv.bz2",
    # Links is huge (~200MB compressed) - we'll use the jpn-eng specific version
    # Tatoeba provides per-language pairs via their API instead
    # We'll use: https://downloads.tatoeba.org/exports/per_language/jpn/jpn-eng_links.tsv.bz2
    "jpn_eng_links": f"{BASE_URL}/per_language/jpn/jpn-eng_links.tsv.bz2",
}

MAX_LEN = 60
MIN_LEN = 5


# ── Grammar detection ─────────────────────────────────────────────────────────

# Causative-receptive (check first — it's a superset):
# - させられ (ichidan causative + receptive)
# - せられ (godan causative + receptive, contracted)
CAUS_RECEP_RE = re.compile(r"させられ[るたてないよう]|せられ[るたてないよう]")

# Causative only (without receptive following):
# - [consonant]せ + verb-ending (godan)
# - させ + verb-ending (ichidan / する)
CAUSATIVE_RE = re.compile(
    r"(?<![られ])させ[るたてないよう]"  # させ not preceded by られ
    r"|[かきたなはまやらわがきだばぱ]せ[るたてないよう]"  # godan: [あ-stem consonant]せる
)

# Receptive only:
# - [consonant]れ (godan: あ-stem + れる)
# - られ (ichidan)
RECEPTIVE_RE = re.compile(
    r"(?<!さ)(?<!せ)(?<!か)られ[るたてないよう]"   # られ not part of causative
    r"|[かさたなはまやらわがざだばぱ]れ[るたてないよう]"  # godan receptive
)

# て-form + giving/receiving (the primary target for these verbs)
TE_AGERU_RE = re.compile(r"[てで]あげ[るたてないよう]|[てで]上げ[るたてないよう]")
TE_KURERU_RE = re.compile(r"[てで]くれ[るたてないよう]|[てで]くれな[かいっ]|[てで]呉れ[るたてないよう]")
TE_MORAU_RE = re.compile(r"[てで]もら[うえっいよ]|[てで]貰[うえっいよ]|[てで]もらえ")

# Standalone giving/receiving (sentence ends with or prominently features them)
AGERU_RE = re.compile(r"あげ[るたてないよう]|上げ[るたてないよう]")
KURERU_RE = re.compile(r"くれ[るたてないよう]|くれな[かいっ]|呉れ[るたてないよう]")
MORAU_RE = re.compile(r"もら[うえっいよ]|貰[うえっいよ]|もらえ[るたなる]")

# Exclusion patterns — overly complex stacked grammar
EXCLUDE_RE = re.compile(
    r"ていただ"          # いただく (keigo morau — out of scope)
    r"|てさしあげ"       # keigo ageru
    r"|にもかかわらず"   # "despite"
    r"|ざるをえな"       # "can't help but"
    r"|ことができ"       # potential auxiliary (distracts focus)
    r"|というのは"       # explanatory construct
    r"|ているところ"     # progressive aspect clause
)


def detect_grammar_points(text: str) -> list[str]:
    points: set[str] = set()

    if CAUS_RECEP_RE.search(text):
        points.update({"causative_receptive", "causative", "receptive"})
    else:
        if CAUSATIVE_RE.search(text):
            points.add("causative")
        if RECEPTIVE_RE.search(text):
            points.add("receptive")

    if TE_AGERU_RE.search(text):
        points.add("ageru")
    elif AGERU_RE.search(text):
        points.add("ageru")

    if TE_KURERU_RE.search(text):
        points.add("kureru")
    elif KURERU_RE.search(text):
        points.add("kureru")

    if TE_MORAU_RE.search(text):
        points.add("morau")
    elif MORAU_RE.search(text):
        points.add("morau")

    return sorted(points)


def is_quality(text: str) -> bool:
    if not (MIN_LEN <= len(text) <= MAX_LEN):
        return False
    # Skip sentences with Roman letters (≥3 consecutive) or long numbers
    if re.search(r"[a-zA-Z]{3,}|\d{4,}", text):
        return False
    if EXCLUDE_RE.search(text):
        return False
    return True


def estimate_jlpt(text: str, grammar_points: list[str]) -> str:
    """Rough JLPT estimate (marked jlpt_estimated=true)."""
    gps = set(grammar_points)
    L = len(text)

    if "causative_receptive" in gps:
        return "N3" if L > 35 else "N4"
    if "causative" in gps and "receptive" not in gps:
        return "N4"
    if "receptive" in gps and "causative" not in gps:
        return "N3" if L > 30 else "N4"
    # giving/receiving verbs
    giving = gps & {"ageru", "kureru", "morau"}
    if giving and not (gps - giving):
        return "N5" if L < 15 else "N4"
    return "N4"


# ── Download helpers ──────────────────────────────────────────────────────────

def fetch_bz2_tsv(name: str, url: str) -> Path:
    """Download and decompress a bz2 TSV, returning the path to the TSV."""
    bz2_path = DATA_DIR / f"{name}.tsv.bz2"
    tsv_path = DATA_DIR / f"{name}.tsv"

    if not tsv_path.exists():
        if not bz2_path.exists():
            print(f"  Downloading {name} from Tatoeba...")
            try:
                urllib.request.urlretrieve(url, bz2_path)
                print(f"    Saved {bz2_path.stat().st_size // 1024:,} KB")
            except Exception as e:
                print(f"    WARNING: Could not download {url}: {e}")
                return None
        print(f"  Decompressing {name}...")
        with bz2.open(bz2_path, "rb") as f_in, open(tsv_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        print(f"  (cached) {name}.tsv")

    return tsv_path


def load_tsv_2col(path: Path) -> dict[str, str]:
    """Load a 2-column TSV into {col0: col1}."""
    result = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                result[parts[0]] = parts[1]
    return result


def load_tsv_3col_text(path: Path) -> dict[str, str]:
    """Load id/lang/text TSV into {id: text}."""
    result = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                result[parts[0]] = parts[2]
    return result


def load_links_multimap(path: Path) -> dict[str, list[str]]:
    """Load two-column TSV as {from_id: [to_id, ...]}."""
    result: dict[str, list[str]] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                result[parts[0]].append(parts[1])
    return dict(result)


def load_tags_multimap(path: Path) -> dict[str, list[str]]:
    """Load sentence_id/tag TSV as {id: [tag, ...]}."""
    result: dict[str, list[str]] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                result[parts[0]].append(parts[1])
    return dict(result)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Tatoeba Sentence Fetcher ===\n")
    print("Downloading Tatoeba data files...")

    jpn_path = fetch_bz2_tsv("jpn_sentences", FILES["jpn_sentences"])
    eng_path = fetch_bz2_tsv("eng_sentences", FILES["eng_sentences"])
    tags_path = fetch_bz2_tsv("jpn_tags", FILES["jpn_tags"])
    links_path = fetch_bz2_tsv("jpn_eng_links", FILES["jpn_eng_links"])

    print("\nLoading data...")
    jpn_sentences = load_tsv_3col_text(jpn_path)
    print(f"  {len(jpn_sentences):,} Japanese sentences")

    eng_sentences = load_tsv_3col_text(eng_path) if eng_path else {}
    print(f"  {len(eng_sentences):,} English sentences")

    tags_map = load_tags_multimap(tags_path) if tags_path else {}
    print(f"  Tags loaded for {len(tags_map):,} sentences")

    links_map = load_links_multimap(links_path) if links_path else {}
    print(f"  Links for {len(links_map):,} Japanese sentences")

    print("\nFiltering sentences...")
    filtered = []
    cat_counts: dict[str, int] = defaultdict(int)
    seen_ids: set[str] = set()

    for sid, text in jpn_sentences.items():
        if not is_quality(text):
            continue
        gps = detect_grammar_points(text)
        if not gps:
            continue

        # Find English translation
        eng_trans = None
        if sid in links_map:
            for eng_id in links_map[sid]:
                if eng_id in eng_sentences:
                    eng_trans = eng_sentences[eng_id]
                    break

        # Skip if no English translation available (needed for the drill)
        if not eng_trans:
            continue

        # JLPT tag from Tatoeba
        sid_tags = tags_map.get(sid, [])
        jlpt_tag = next((t for t in sid_tags if t.startswith("@")), None)
        if jlpt_tag:
            # Tatoeba JLPT tags look like "@JLPT3" or "JLPT-N3"
            m = re.search(r"N?(\d)", jlpt_tag)
            jlpt_level = f"N{m.group(1)}" if m else estimate_jlpt(text, gps)
            jlpt_estimated = False
        else:
            jlpt_level = estimate_jlpt(text, gps)
            jlpt_estimated = True

        entry = {
            "id": f"tat_{sid}",
            "tatoeba_id": sid,
            "japanese": text,
            "natural_english": eng_trans,
            "grammar_points": gps,
            "jlpt_level": jlpt_level,
            "jlpt_estimated": jlpt_estimated,
            "source": "tatoeba",
            # Placeholders to be filled by generate_content.py
            "cure_dolly_gloss": "",
            "answer_breakdown": "",
            "word_map": [],
        }
        filtered.append(entry)
        seen_ids.add(sid)

        for gp in gps:
            cat_counts[gp] += 1

    print(f"\nFound {len(filtered):,} candidates with English translations")
    print("\nCounts per grammar point (sentence can appear in multiple):")
    for cat in ["receptive", "causative", "causative_receptive", "ageru", "kureru", "morau"]:
        print(f"  {cat:25s}: {cat_counts.get(cat, 0):,}")

    # Sort: causative_receptive first, then by grammar_points length (combinations), then JLPT
    jlpt_order = {"N5": 0, "N4": 1, "N3": 2, "N2": 3, "N1": 4}
    filtered.sort(key=lambda e: (
        "causative_receptive" not in e["grammar_points"],
        -len(e["grammar_points"]),
        jlpt_order.get(e["jlpt_level"], 3),
        len(e["japanese"]),
    ))

    out_path = DATA_DIR / "tatoeba_filtered.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(filtered):,} filtered sentences → {out_path}")
    return out_path


if __name__ == "__main__":
    main()
