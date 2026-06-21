/**
 * Generate hiragana readings for all sentences using kuromoji.
 * Readings are used for fuzzy answer matching (accept hiragana even if correct answer has kanji).
 *
 * Usage: node scripts/generate_readings.js
 */

const kuromoji = require("kuromoji");
const fs = require("fs");
const path = require("path");

const DATA_DIR = path.join(__dirname, "..", "data");

function tokenize(tokenizer, text) {
  return tokenizer.tokenize(text);
}

function toHiragana(str) {
  // Convert katakana to hiragana
  return str.replace(/[ァ-ヶ]/g, (ch) =>
    String.fromCharCode(ch.charCodeAt(0) - 0x60)
  );
}

function getReading(tokens) {
  return tokens
    .map((t) => {
      const r = t.reading || t.surface_form;
      return toHiragana(r);
    })
    .join("");
}

function getVocab(tokens) {
  const seen = new Set();
  return tokens
    .filter((t) => {
      const pos = t.pos;
      if (pos === "助詞" || pos === "助動詞" || pos === "記号" ||
          pos === "接続詞" || pos === "感動詞" || pos === "接頭詞") return false;
      // exclude dependent nouns (の、こと、もの as standalone)
      if (pos === "名詞" && t.pos_detail_1 === "非自立") return false;
      if (pos === "名詞" && t.pos_detail_1 === "数") return false;
      if (t.surface_form === "*" || t.surface_form.trim() === "") return false;
      return true;
    })
    .map((t) => {
      const word = (t.basic_form && t.basic_form !== "*") ? t.basic_form : t.surface_form;
      const reading = toHiragana(t.reading || t.surface_form);
      return { word, reading };
    })
    .filter((v) => {
      if (seen.has(v.word)) return false;
      seen.add(v.word);
      // only show words containing kanji — kana-only words don't need a reading hint
      return /[一-龯]/.test(v.word);
    });
}

kuromoji
  .builder({ dicPath: path.join(__dirname, "..", "node_modules", "kuromoji", "dict") })
  .build((err, tokenizer) => {
    if (err) {
      console.error("Kuromoji build error:", err);
      process.exit(1);
    }

    const inputPath = path.join(DATA_DIR, "sentences.json");
    const sentences = JSON.parse(fs.readFileSync(inputPath, "utf-8"));

    console.log(`Generating readings for ${sentences.length} sentences...`);

    let count = 0;
    for (const entry of sentences) {
      const tokens = tokenize(tokenizer, entry.japanese);
      entry.reading = getReading(tokens);
      entry.vocab = getVocab(tokens);
      count++;
      if (count % 100 === 0) {
        process.stdout.write(`  ${count}/${sentences.length}\r`);
      }
    }

    console.log(`\nDone. ${count} readings generated.`);

    fs.writeFileSync(inputPath, JSON.stringify(sentences, null, 2), "utf-8");
    console.log(`Updated ${inputPath}`);

    // Show samples
    console.log("\nSamples:");
    for (const e of sentences.slice(0, 5)) {
      console.log(`  JP: ${e.japanese}`);
      console.log(`  RD: ${e.reading}`);
      console.log();
    }
  });
