from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from atlas.nlp import config as nlp_config
from atlas.nlp import eval_analysis, eval_stages


def synthetic(tmp_path: Path) -> None:
    """Write stage files for 160 mentions with a known structure."""
    rng = random.Random(1)
    d = tmp_path
    sample, bela, fuzzy, blink, llm, info = [], [], [], [], [], []
    qids = [f"Q{i}" for i in range(1, 60)]
    for n in range(160):
        lang = "en" if n % 2 == 0 else "de"
        split = "dev" if n < 60 else "test"
        gold = qids[n % 40]
        in_kb = n % 10 != 0  # one in ten is out of the index
        mid = f"{lang}-{split}-{n}"
        text = f"Der Besuch von Name{n} war lang."
        sample.append(
            {
                "id": mid,
                "lang": lang,
                "split": split,
                "text": text,
                "start": 13,
                "end": 13 + len(f"Name{n}"),
                "gold": gold,
            }
        )
        # BELA: good for most, confusable for some, weak for out-of-index
        top = gold if in_kb and rng.random() < 0.8 else qids[(n + 7) % 59]
        other = qids[(n + 3) % 59]
        score = rng.uniform(0.75, 0.97) if n % 3 else rng.uniform(0.45, 0.65)
        cands = [
            {"qid": top, "score": score, "retrieval": score},
            {"qid": other, "score": score - rng.uniform(0.02, 0.4), "retrieval": 0.1},
        ]
        if in_kb and gold not in (top, other):
            cands.append({"qid": gold, "score": score - 0.45, "retrieval": 0.1})
        if not in_kb:
            cands = [{"qid": top, "score": rng.uniform(0.1, 0.35), "retrieval": 0.1}]
        bela.append(
            {
                "id": mid,
                "in_kb": in_kb,
                "aligned": True,
                "cands": cands,
                "detected": [{"start": 13, "end": 14 + n, "md": 0.9, "top": []}],
            }
        )
        fuzzy.append({"id": mid, "cands": [{"qid": other, "score": 0.9}]})
        if lang == "en":
            probs = {c["qid"]: (0.9 if c["qid"] == gold else 0.05) for c in cands}
            blink.append({"id": mid, "bela": probs, "fuzzy": {other: 1.0}})
        else:
            llm.append(
                {"id": mid, "choice": gold if in_kb else None, "confidence": 0.8, "ok": True}
            )
    for q in qids:
        info.append(
            {
                "qid": q,
                "title": q,
                "description": "d",
                "kind": "person",
                "missing": False,
                "labels": {"en": f"Label {q}"},
                "aliases": {},
            }
        )
    for name, rows in (
        ("sample", sample),
        ("bela", bela),
        ("fuzzy", fuzzy),
        ("blink", blink),
        ("llm", llm),
        ("entity_info", info),
    ):
        (d / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_analysis_end_to_end_on_synthetic_stage_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic(tmp_path)
    monkeypatch.setattr(eval_stages, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(eval_analysis, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(eval_analysis, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(nlp_config, "CONFIG_DIR", tmp_path / "config")
    results = eval_analysis.analyze({"generated": "test"})
    assert results["n_dev"] == 60 and results["n_test"] == 100
    assert results["tuned"]["hybrid"]["f1"] >= results["tuned"]["rapidfuzz"]["f1"]
    assert 0 <= results["tuned_routes"]["accept"] <= 1
    assert abs(sum(results["tuned_routes"].values()) - 1) < 1e-9
    assert (tmp_path / "docs" / "eval" / "report.md").read_text().startswith("# Entity linking")
    assert json.loads((tmp_path / "public" / "metrics.json").read_text())["n_test"] == 100
    assert (tmp_path / "config" / "nlp_config_v1.json").exists()
    assert (tmp_path / "docs" / "eval" / "reliability.png").exists()
