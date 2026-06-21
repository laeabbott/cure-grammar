"use strict";

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  allSentences: [],   // full loaded bank
  session: [],        // sentences for this session
  index: 0,           // current position in session
  results: [],        // { entry, userAnswer, correct }
  settings: {
    grammarPoints: ["receptive","causative","causative_receptive","ageru","kureru","morau"],
    jlptLevels: ["N5","N4","N3","N2","N1"],
    count: 10,
  },
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const screens = {
  loading:  $("screen-loading"),
  settings: $("screen-settings"),
  drill:    $("screen-drill"),
  result:   $("screen-result"),
  summary:  $("screen-summary"),
};

// ── Screen management ─────────────────────────────────────────────────────────
function showScreen(name) {
  Object.values(screens).forEach(s => s.classList.remove("active"));
  screens[name].classList.add("active");
  // Scroll to top on each screen transition
  window.scrollTo(0, 0);
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadData() {
  try {
    const resp = await fetch("data/sentences.json");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    state.allSentences = await resp.json();
    showScreen("settings");
    updateSentenceCount();
  } catch (e) {
    screens.loading.querySelector("p").textContent =
      "Failed to load sentence bank. Please check that data/sentences.json exists.";
    console.error(e);
  }
}

// ── Settings logic ────────────────────────────────────────────────────────────
const GP_LABELS = {
  receptive: "Receptive (られる)",
  causative: "Causative (させる)",
  causative_receptive: "Causative-receptive",
  ageru: "あげる",
  kureru: "くれる",
  morau: "もらう",
};

function readSettings() {
  const gpChecks = [...document.querySelectorAll("input[name='gp']")]
    .filter(c => c.checked).map(c => c.value);
  const jlptChecks = [...document.querySelectorAll("input[name='jlpt']")]
    .filter(c => c.checked).map(c => c.value);
  const countVal = document.querySelector("input[name='count']:checked")?.value || "10";

  state.settings.grammarPoints = gpChecks.length ? gpChecks : ["receptive","causative","causative_receptive","ageru","kureru","morau"];
  state.settings.jlptLevels = jlptChecks.length ? jlptChecks : ["N5","N4","N3","N2","N1"];
  state.settings.count = countVal === "all" ? Infinity : parseInt(countVal, 10);
}

function filterSentences() {
  const { grammarPoints, jlptLevels } = state.settings;
  return state.allSentences.filter(e =>
    e.grammar_points.some(gp => grammarPoints.includes(gp)) &&
    jlptLevels.includes(e.jlpt_level)
  );
}

function updateSentenceCount() {
  readSettings();
  const available = filterSentences();
  const count = Math.min(available.length, state.settings.count === Infinity ? available.length : state.settings.count);
  $("sentence-count-info").textContent =
    available.length === 0
      ? "No sentences match these filters."
      : `${available.length} sentences available — ${count} will be used this session`;
  $("btn-start").disabled = available.length === 0;
}

// "All" checkbox sync
function setupAllCheckbox(allId, groupName) {
  const allCb = $(allId);
  const groupCbs = () => [...document.querySelectorAll(`input[name='${groupName}']`)];

  allCb.addEventListener("change", () => {
    groupCbs().forEach(c => { c.checked = allCb.checked; });
    updateSentenceCount();
  });

  groupCbs().forEach(c => c.addEventListener("change", () => {
    allCb.checked = groupCbs().every(c => c.checked);
    updateSentenceCount();
  }));
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function startSession() {
  readSettings();
  const filtered = filterSentences();
  if (filtered.length === 0) return;

  const sessionSize = state.settings.count === Infinity
    ? filtered.length
    : Math.min(state.settings.count, filtered.length);

  state.session = shuffle(filtered).slice(0, sessionSize);
  state.index = 0;
  state.results = [];

  showScreen("drill");
  renderDrillItem();
}

function startSessionFromList(sentences) {
  state.session = shuffle(sentences);
  state.index = 0;
  state.results = [];
  showScreen("drill");
  renderDrillItem();
}

// ── Answer checking ───────────────────────────────────────────────────────────

function katakanaToHiragana(str) {
  return str.replace(/[ァ-ヶ]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0x60));
}

function normalizeAnswer(str) {
  return str
    .trim()
    .replace(/\s+/g, "")                    // remove all whitespace
    .replace(/[。．]/g, "。")               // normalize sentence-final punctuation
    .replace(/[、，]/g, "、")              // normalize commas
    .replace(/[！!]/g, "！")
    .replace(/[？?]/g, "？")
    .replace(/[・•]/g, "・")
    .replace(/[〜～]/g, "〜")
    .replace(/[ー－]/g, "ー")
    // strip trailing sentence-ending punctuation for comparison
    .replace(/[。！？]$/, "");
}

function toHiragana(str) {
  return katakanaToHiragana(str).toLowerCase();
}

// Grammatical particles only — sentence-final (よ、ね、ぞ、わ etc.) excluded
// to avoid false positives when user omits or varies tone particles
const WORKER_URL = "https://cure-grammar-proxy.im-b5f.workers.dev";

const PARTICLE_RE = /[はがをにでとものへ]|から|まで|より/g;

function extractParticles(str) {
  const matches = [];
  let m;
  while ((m = PARTICLE_RE.exec(str)) !== null) {
    matches.push({ idx: m.index, val: m[0] });
  }
  PARTICLE_RE.lastIndex = 0;
  return matches;
}

function checkAnswer(entry, userRaw) {
  const user = normalizeAnswer(userRaw);
  const correct = normalizeAnswer(entry.japanese);
  const reading = normalizeAnswer(entry.reading || "");

  if (!user) return { correct: false, reason: "empty" };

  const userHira = toHiragana(user);
  const correctHira = toHiragana(correct);
  const readingHira = toHiragana(reading);

  // Exact match
  if (user === correct) return { correct: true };

  // Match against hiragana reading (kana/kanji variant tolerance)
  if (userHira === correctHira || userHira === readingHira) return { correct: true };

  // Partial acceptance: if the hiragana representation of the user's answer
  // matches the hiragana reading of the correct answer, accept it.
  // This handles: user types kanji differently but pronounces correctly.
  if (reading && userHira === readingHira) return { correct: true };

  // Casual いい ↔ ー equivalence (Tatoeba sometimes transcribes elongated いい as ー)
  const casually = s => s.replace(/いい/g, "ー");
  if (casually(userHira) === casually(correctHira) ||
      casually(userHira) === casually(readingHira)) return { correct: true };

  // Fuzzy match: compare in the same writing system to avoid kanji-vs-hiragana mismatch.
  // If the user typed kanji, compare against the kanji answer; if pure kana, compare against reading.
  const userHasKanji = /[一-龯]/.test(user);
  const ref = userHasKanji ? correct : (readingHira || correctHira);

  const particlesCorrect = checkParticlesMatch(user, ref);
  const contentSimilarity = cosineSimilarity(user, ref);

  if (particlesCorrect) {
    // Accept normal close match OR valid word-order variation (same chars rearranged)
    const sortedSim = cosineSimilarity(
      user.split("").sort().join(""),
      ref.split("").sort().join("")
    );
    if (contentSimilarity >= 0.80 || sortedSim >= 0.92) return { correct: true };
  }

  if (!particlesCorrect) return { correct: false, reason: "particle_error" };
  return { correct: false, reason: "wrong" };
}

function checkParticlesMatch(a, b) {
  // Only flag a mismatch when the same word appears in both answers with different particles.
  // Missing words (present in b but absent in a) are not a particle error — they're missing content.
  const aPairs = extractParticlePairs(a);
  const bPairs = extractParticlePairs(b);
  for (const [word, aParticle] of aPairs) {
    const bParticle = bPairs.get(word);
    if (bParticle !== undefined && bParticle !== aParticle) return false;
  }
  return true;
}

// Explanations for wrong→correct particle swaps. {w} = the word in question.
const PARTICLE_EXPLANATIONS = {
  "を": {
    "は": "{w}は — は marks the topic ('as for {w}…'). を marks the object of an other-move verb — the thing directly acted upon. The topic isn't 'acted upon' in that sense — it's what the sentence is about.",
    "が": "{w}が — が marks the grammatical subject (who acts or experiences). を marks the direct object. Here {w} is the actor or experiencer, not what's acted upon.",
    "に": "{w}に — に marks direction, destination, or the agent/source in a receptive. を marks a direct object that the verb acts on directly.",
    "で": "{w}で — で marks the means, method, or location. を marks the direct object of the action itself.",
    "も": "{w}も — も means 'also / too', extending the statement to {w} as well. を marks the direct object.",
  },
  "が": {
    "は": "{w}は — は marks the topic. が marks the specific grammatical subject. Use は when {w} is what the sentence is about; が when singling out who specifically acts or experiences.",
    "を": "{w}を — を marks the direct object. が marks the subject/actor. Here {w} is what the action is directed at, not who performs it.",
    "に": "{w}に — に marks direction, target, or the causer's target in a causative. が marks the subject/actor.",
    "で": "{w}で — で marks means or location. が marks who acts.",
  },
  "は": {
    "が": "{w}が — が marks the grammatical subject, singling out specifically who acts. は marks the topic, which is more general. Use が here to specifically identify who performs the action.",
    "を": "{w}を — を marks the direct object of the action. は marks the topic. Here {w} is what's directly acted upon by the verb.",
    "に": "{w}に — に marks direction, target, or recipient. は marks the topic. Here {w} is the destination or recipient of the action.",
    "で": "{w}で — で marks the means, method, or location of an action. は marks the topic.",
  },
  "に": {
    "は": "{w}は — は marks the topic. に marks direction, target, or recipient. Here {w} is where the action goes or who receives it.",
    "が": "{w}が — が marks the subject/actor. に marks the target or recipient. Here {w} receives or is directed toward.",
    "を": "{w}を — を marks the direct object. に marks direction or target. Here {w} is where the action is directed, not what's directly acted upon.",
    "で": "{w}で — で marks the means or location of the action. に marks the destination or recipient.",
    "から": "{w}から — から marks the source or starting point ('from'). に marks the destination.",
  },
  "で": {
    "は": "{w}は — は marks the topic. で marks the means, method, or location. Here {w} is how or where the action takes place.",
    "が": "{w}が — が marks the subject. で marks the means or location of the action.",
    "を": "{w}を — を marks the direct object. で marks the means or method. {w} is the tool or location, not what's directly acted upon.",
    "に": "{w}に — に marks direction or destination. で marks means or location. Here {w} describes how the action is done, not where it ends up.",
  },
  "も": {
    "は": "{w}も — も means 'also / too', adding {w} to what was already said. は simply marks the topic without the additive nuance.",
    "が": "{w}も — も adds {w} with an 'also' nuance. が marks the specific actor/subject.",
    "を": "{w}も — も adds {w} with 'also', replacing を when extending the direct object.",
  },
  "の": {
    "が": "{w}の — の can replace が as a subject marker inside a subordinate clause. Use が at the top level; の marks the subject within a relative/subordinate clause.",
  },
};

// Grammar-point context appended when it helps explain the particle choice
const GRAMMAR_PARTICLE_CONTEXT = {
  "receptive": {
    "を→は": "In a receptive (れる/られる) sentence the receiver is marked は or が — not を. を marks the object of an other-move verb, but the receptive helper shifts the role: the subject 'gets' the action done to it.",
    "を→が": "In a receptive (れる/られる) sentence the receiver is marked が or は, not を.",
    "が→は": "In a receptive sentence the receiver can be topicalized with は instead of が.",
  },
  "causative": {
    "が→に": "In a causative (せる/させる) sentence the person caused to act is marked に (or を for some verbs). が marks the causer.",
    "を→に": "に marks who is caused to act when their verb is other-move (他動詞) — using を for the caused person would clash with を already marking the verb's object. を marks the caused person only when their verb is self-move (自動詞) and has no competing object.",
  },
  "morau": {
    "が→に": "With もらう the giver/source is marked に ('received from'). が marks the receiver (the subject).",
  },
};

function extractParticlePairs(str) {
  const pairs = new Map();
  const RE = /([一-龯ぁ-んァ-ン]{1,4}?)([はがをにでとものへ]|から|まで|より)/g;
  let m;
  while ((m = RE.exec(str)) !== null) {
    if (m[1]) pairs.set(m[1], m[2]);
  }
  return pairs;
}

function buildParticleHint(userAnswer, entry) {
  const user = normalizeAnswer(userAnswer);
  const correct = normalizeAnswer(entry.japanese);
  const userPairs = extractParticlePairs(user);
  const correctPairs = extractParticlePairs(correct);
  const gps = entry.grammar_points || [];

  const hints = [];
  for (const [word, correctP] of correctPairs) {
    const userP = userPairs.get(word);
    if (userP === undefined || userP === correctP) continue;

    // Look up specific explanation for this wrong→correct swap
    let explanation = PARTICLE_EXPLANATIONS[userP]?.[correctP]
      || `${word}${correctP} (not ${word}${userP})`;
    explanation = explanation.replace(/\{w\}/g, word);

    // Check for grammar-point context that adds more precision
    const swapKey = `${userP}→${correctP}`;
    for (const gp of gps) {
      const ctx = GRAMMAR_PARTICLE_CONTEXT[gp]?.[swapKey];
      if (ctx) { explanation += " " + ctx; break; }
    }

    hints.push(explanation);
  }

  return hints.length > 0 ? hints.join("\n") : null;
}

function cosineSimilarity(a, b) {
  // Simple character n-gram overlap
  const ngrams = (s, n) => {
    const g = new Map();
    for (let i = 0; i <= s.length - n; i++) {
      const key = s.slice(i, i + n);
      g.set(key, (g.get(key) || 0) + 1);
    }
    return g;
  };
  const ga = ngrams(a, 2);
  const gb = ngrams(b, 2);
  let dot = 0, magA = 0, magB = 0;
  for (const [k, v] of ga) { magA += v * v; if (gb.has(k)) dot += v * gb.get(k); }
  for (const [, v] of gb) magB += v * v;
  if (magA === 0 || magB === 0) return 0;
  return dot / Math.sqrt(magA * magB);
}

// ── Drill rendering ───────────────────────────────────────────────────────────
function renderDrillItem() {
  const entry = state.session[state.index];
  const total = state.session.length;
  const pct = (state.index / total) * 100;

  // Header
  $("progress-bar").style.width = pct + "%";
  $("progress-label").textContent = `${state.index + 1} / ${total}`;

  // GP badges hidden during drill (would give away the answer)
  $("drill-gp-badges").innerHTML = "";

  // Natural English with word-map spans
  renderEnglishWithMap(entry);

  // Gloss
  $("drill-gloss").textContent = entry.cure_dolly_gloss || "(no gloss available)";

  // Vocab accordion — populate and reset to closed
  const vocabList = $("drill-vocab-list");
  const accordion = $("drill-vocab-accordion");
  const vocab = entry.vocab || [];
  if (vocab.length > 0) {
    vocabList.innerHTML = vocab.map(v =>
      `<span class="vocab-item">${v.word} <span class="vocab-reading">(${v.reading})</span></span>`
    ).join("");
    accordion.hidden = false;
    accordion.open = false;
  } else {
    accordion.hidden = true;
  }

  // Clear input
  const input = $("answer-input");
  input.value = "";
  input.classList.remove("correct", "incorrect");
  input.disabled = false;
  $("btn-submit").disabled = false;
  $("btn-submit").textContent = "Check";

  // Focus input (after slight delay on mobile to avoid layout jump)
  setTimeout(() => {
    if (window.matchMedia("(min-width: 600px)").matches) {
      input.focus();
    }
  }, 120);

}

function renderEnglishWithMap(entry) {
  $("drill-natural-en").textContent = entry.natural_english;
}

// ── Submit ────────────────────────────────────────────────────────────────────
function submitAnswer() {
  const entry = state.session[state.index];
  const userAnswer = $("answer-input").value;

  if (!userAnswer.trim()) return;

  const result = checkAnswer(entry, userAnswer);
  state.results.push({ entry, userAnswer, correct: result.correct, reason: result.reason });

  // Visual feedback on input
  $("answer-input").classList.add(result.correct ? "correct" : "incorrect");
  $("answer-input").disabled = true;
  $("btn-submit").disabled = true;

  // Short delay then show result
  setTimeout(() => showResult(entry, userAnswer, result), 350);
}

// ── Result screen ─────────────────────────────────────────────────────────────
function showResult(entry, userAnswer, result) {
  const isCorrect = result.correct;
  const total = state.session.length;
  const pct = ((state.index + 1) / total) * 100;

  $("result-progress-bar").style.width = pct + "%";
  $("result-progress-label").textContent = `${state.index + 1} / ${total}`;

  // Verdict
  const verdict = $("result-verdict");
  verdict.className = "verdict " + (isCorrect ? "correct" : "incorrect");
  verdict.textContent = isCorrect ? randomPraise() : "Not quite";

  // User answer
  const userEl = $("result-user-answer");
  userEl.textContent = userAnswer || "(no answer)";
  userEl.className = "result-text user-answer-text " + (isCorrect ? "correct-answer" : "wrong-answer");

  // Correct answer row (hidden if correct)
  const correctRow = $("result-correct-row");
  if (isCorrect) {
    correctRow.classList.add("hidden");
  } else {
    correctRow.classList.remove("hidden");
    $("result-correct").textContent = entry.japanese;
  }

  // Error hint — specific to what went wrong
  const hintEl = $("result-error-hint");
  if (isCorrect) {
    hintEl.hidden = true;
    hintEl.textContent = "";
  } else if (result.reason === "particle_error") {
    const hint = buildParticleHint(userAnswer, entry);
    hintEl.textContent = hint || "Particle mismatch — check which particles attach to which words.";
    hintEl.hidden = false;
  } else if (result.reason === "wrong") {
    hintEl.textContent = "Content doesn't match — check vocabulary and word order.";
    hintEl.hidden = false;
  } else {
    hintEl.hidden = true;
  }

  // Explain button — only on wrong answers
  const explainContainer = $("result-explain-container");
  const explainText = $("result-explain-text");
  if (isCorrect) {
    explainContainer.hidden = true;
  } else {
    explainContainer.hidden = false;
    explainText.hidden = true;
    explainText.textContent = "";
    $("btn-explain").disabled = false;
    $("btn-explain").textContent = "Explain this mistake";
    $("btn-explain").onclick = () => explainMistake(entry, userAnswer, result);
  }

  // Reminders
  const bdEl = $("result-breakdown");
  const breakdown = entry.answer_breakdown || "";
  if (breakdown) {
    const sentences = breakdown.split(". ").filter(s => s.trim());
    const ul = document.createElement("ul");
    ul.className = "breakdown-list";
    for (const s of sentences) {
      const li = document.createElement("li");
      li.textContent = s.endsWith(".") || s.endsWith("。") ? s : s + ".";
      ul.appendChild(li);
    }
    bdEl.innerHTML = "";
    bdEl.appendChild(ul);
  } else {
    bdEl.innerHTML = "";
  }

  // Next button label
  const isLast = state.index >= state.session.length - 1;
  $("btn-next").textContent = isLast ? "View Results" : "Next →";

  showScreen("result");
}

async function explainMistake(entry, userAnswer, result) {
  const btn = $("btn-explain");
  const el = $("result-explain-text");

  btn.disabled = true;
  btn.textContent = "Thinking…";
  el.hidden = false;
  el.textContent = "";

  const errorType = result.reason === "particle_error"
    ? "particle/marker error"
    : "vocabulary or content mismatch";

  const prompt = `The student was asked to translate this into Japanese:
"${entry.natural_english}"

Correct answer: ${entry.japanese}
Student's answer: ${userAnswer || "(no answer)"}
Error type: ${errorType}
Grammar points: ${(entry.grammar_points || []).join(", ")}
Cure Dolly gloss: ${entry.cure_dolly_gloss || ""}

Explain in 2–3 sentences exactly what went wrong and why, using Cure Dolly's framework.`;

  try {
    const res = await fetch(WORKER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: [{ role: "user", content: prompt }] }),
    });
    const data = await res.json();
    el.textContent = data.content?.[0]?.text
      || "Sorry, couldn't generate an explanation.";
  } catch {
    el.textContent = "Couldn't reach the explanation service — check your connection.";
  }

  btn.textContent = "Explain this mistake";
  btn.disabled = false;
}

function randomPraise() {
  const msgs = ["Correct!", "Nice!", "Well done!", "Perfect!", "Exactly right!"];
  return msgs[Math.floor(Math.random() * msgs.length)];
}

// ── Session summary ───────────────────────────────────────────────────────────
function showSummary() {
  const total = state.results.length;
  const correct = state.results.filter(r => r.correct).length;
  const pct = total > 0 ? Math.round((correct / total) * 100) : 0;

  $("summary-score").textContent = `${correct} / ${total}`;
  $("summary-score-label").textContent = `${pct}% correct`;

  // Per-category breakdown
  const cats = {};
  for (const gp of ["causative_receptive","causative","receptive","ageru","kureru","morau"]) {
    cats[gp] = { total: 0, correct: 0 };
  }
  for (const r of state.results) {
    for (const gp of r.entry.grammar_points) {
      if (gp in cats) {
        cats[gp].total++;
        if (r.correct) cats[gp].correct++;
      }
    }
  }

  const breakdownEl = $("summary-breakdown");
  breakdownEl.innerHTML = "";
  for (const [gp, data] of Object.entries(cats)) {
    if (data.total === 0) continue;
    const pctCat = Math.round((data.correct / data.total) * 100);
    const cls = pctCat >= 80 ? "good" : pctCat >= 50 ? "ok" : "bad";
    const row = document.createElement("div");
    row.className = "summary-breakdown-row";
    row.innerHTML = `
      <span class="cat">${GP_LABELS[gp] || gp}</span>
      <span class="stats ${cls}">${data.correct}/${data.total} (${pctCat}%)</span>
    `;
    breakdownEl.appendChild(row);
  }

  // Missed sentences
  const missed = state.results.filter(r => !r.correct).map(r => r.entry);
  const missedEl = $("summary-missed");
  const missedList = $("summary-missed-list");
  const retryBtn = $("btn-retry-missed");

  if (missed.length > 0) {
    missedEl.classList.remove("hidden");
    retryBtn.classList.remove("hidden");
    missedList.innerHTML = "";
    missed.forEach(entry => {
      const item = document.createElement("div");
      item.className = "missed-item";
      item.innerHTML = `
        <div class="missed-jp">${entry.japanese}</div>
        <div class="missed-en">${entry.natural_english}</div>
        <div class="missed-gp">${entry.grammar_points.map(g => GP_LABELS[g] || g).join(" · ")}</div>
      `;
      missedList.appendChild(item);
    });
    retryBtn.onclick = () => startSessionFromList(missed);
  } else {
    missedEl.classList.add("hidden");
    retryBtn.classList.add("hidden");
  }

  showScreen("summary");
}

// ── Event wiring ──────────────────────────────────────────────────────────────
function init() {
  // Settings
  setupAllCheckbox("gp-all", "gp");
  setupAllCheckbox("jlpt-all", "jlpt");

  document.querySelectorAll("input[name='count']").forEach(r => {
    r.addEventListener("change", updateSentenceCount);
  });

  $("btn-start").addEventListener("click", startSession);

  // Drill
  $("btn-submit").addEventListener("click", submitAnswer);

  $("answer-input").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!$("btn-submit").disabled) submitAnswer();
    }
  });

  // Quit buttons
  $("btn-quit").addEventListener("click", () => {
    if (confirm("Quit this session?")) showScreen("settings");
  });
  $("btn-result-quit").addEventListener("click", () => {
    if (confirm("Quit this session?")) showScreen("settings");
  });

  // Result → next / summary
  $("btn-next").addEventListener("click", () => {
    if (state.index >= state.session.length - 1) {
      showSummary();
    } else {
      state.index++;
      showScreen("drill");
      renderDrillItem();
    }
  });

  // Summary
  $("btn-restart").addEventListener("click", () => showScreen("settings"));

  // Load data
  loadData();
}

document.addEventListener("DOMContentLoaded", init);
