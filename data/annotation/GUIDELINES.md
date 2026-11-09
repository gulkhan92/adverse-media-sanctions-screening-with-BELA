# Gold set annotation guidelines

Target: 300 to 400 sentences in en, es, fr, de, ar, ru, tr, committed as
`data/gold/*.jsonl`. At least 60 hard collision cases. Two annotators label a
random 20 percent of the sentences so agreement can be measured.

## Setup

1. Install Label Studio Community: `pip install label-studio` (Apache-2.0).
2. Start it with `label-studio start` and create a project.
3. Paste `label_studio_config.xml` into the labeling interface settings.
4. Import tasks as JSON with fields `text` and `lang`. Draw sentences from the
   harvested news in `data/raw/news/gdelt/` (use the fetched snippets) and from
   Mewsli-X for balance. Do not include full article bodies.

## What to label

1. **Mention spans.** Mark each person or organization name. Include the full
   name as written. Exclude titles that are not part of the name (for example
   "President"), except where the title is conventionally part of the name.
2. **Entity.** Search wikidata.org and enter the QID of the entity the mention
   refers to in this sentence. Choose `NIL` if no Wikidata entity exists. Do not
   guess: when the sentence does not identify the entity, choose NIL.
3. **Adverse category.** One value per sentence describing the adverse
   allegation: fraud, corruption and bribery, money laundering, sanctions
   evasion, terrorism and extremism, organized crime, regulatory action, other,
   or none. Label what the sentence states, not what you know about the subject.
4. **Subject match.** When the sentence is paired with a screened subject
   (provided in the task), mark `true_match` if the mention refers to that
   subject and `collision` if it refers to a different person or organization
   with a similar name. Use `not_applicable` otherwise.
5. **Hard case tags.** Tag common names, transliteration variants (for example
   Latin versus Cyrillic or Arabic spellings), and companies named after people.

## Rules

- Label from the sentence only. Allegations are not findings of guilt.
- Keep notes neutral and factual.
- Resolve disagreements by discussion and record the final label in the export.
- Export as JSON, then convert to JSONL with one object per sentence:
  `{"id", "lang", "text", "mentions": [{"start", "end", "surface", "qid", "type"}], "category", "subject_match", "hard_case"}`.
- Report agreement (Cohen's kappa) for linking decisions and category on the
  double-annotated subset in `data/gold/README.md`.

## Status

Annotation has not started. `data/gold/` holds no labeled data yet. No evaluation
result may cite the gold set until it is complete.
