# Cure Grammar

A Japanese sentence-production drill app built around the [Cure Dolly structural grammar framework](https://kellenok.github.io/cure-script/).

Rather than translating grammar terms into English labels ("passive voice", "causative form"), Cure Dolly teaches the **logical structure** of Japanese: れる/られる means your subject *gets* or *receives* the action; せる/させる means you *cause* someone to act; くれる/あげる/もらう are directional giving verbs that encode social relationships into the verb itself.

This app drills those structures by showing you the English meaning and a structural gloss, and asking you to produce the Japanese sentence.

## Features

- ~955 sentences from [Tatoeba](https://tatoeba.org) covering six grammar areas
- Cure Dolly-style glosses and breakdowns for every sentence
- Fuzzy answer checking: tolerates kanji/kana variation but enforces particle accuracy
- Word-level vocabulary hints (hover desktop / tap mobile)
- Per-category score breakdown and missed-sentence review
- Dark theme, mobile-first, works offline (static site)

## Grammar coverage

| Category | Construction | Cure Dolly framing |
|---|---|---|
| Receptive (受身) | れる / られる | "receive/get" helper — subject gets the action |
| Causative (使役) | せる / させる | "cause" helper — two verbs, two actors |
| Causative-receptive | させられる | Three verbs, two actors — subject receives being-caused-to-do |
| くれる | てくれる | Give downward — someone gives action *to my sphere* |
| あげる | てあげる | Give upward — I give action *to someone outside my sphere* |
| もらう | てもらう | Pull-receive — I pull/receive someone's action |

## Deploying to GitHub Pages

1. Create a repo and push all files (the `data/sentences.json` must be committed)
2. In repo Settings → Pages, set source to **Deploy from a branch → main → / (root)**
3. The `.nojekyll` file prevents GitHub from running Jekyll over the JS files

## Regenerating the sentence bank

Requires Python 3.10+ and Node.js 18+.

```bash
npm install
npm run build       # fetch → curate → generate → readings
```

Steps:
1. `fetch` — downloads Tatoeba exports and filters to ~18k grammar candidates
2. `curate` — applies quality filters and category balancing → ~955 sentences
3. `generate` — builds glosses, breakdowns, and word maps (template-based, no API needed)
4. `readings` — adds hiragana readings via kuromoji (for answer matching)

### Optional: API-enhanced glosses

```bash
python scripts/generate_content.py --api-key sk-ant-...
```

Uses `claude-haiku-4-5` to generate higher-quality Cure Dolly glosses. Without an API key the script uses template-based generation, which is good enough for most sentences.

### Adding grammar categories

1. Add detection regex to `scripts/fetch_tatoeba.py` (`GRAMMAR_PATTERNS`)
2. Add category target to `scripts/curate_sentences.py` (`TARGETS`)
3. Add breakdown template to `scripts/generate_content.py` (`BREAKDOWN_TEMPLATES`)
4. Add chip to `index.html` settings screen

## Credits

- Grammar framework: [Cure Dolly / Organic Japanese](https://kellenok.github.io/cure-script/) (archived by Kellen Parker)
- Sentences: [Tatoeba](https://tatoeba.org) (CC BY 2.0 FR)
- Morphological analysis: [kuromoji](https://github.com/takuyaa/kuromoji)
