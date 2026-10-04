"""Turns result files into docs/EVALUATION.md. Numbers are copied, never restated by hand."""

from __future__ import annotations

from typing import Any

Json = dict[str, Any]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
        "",
    ]


def _label(report: Json) -> str:
    engines = report["engines"]
    agents = engines["chat_model"] if engines["agent_engine"] == "openai" else "offline rules"
    embedder = (
        engines["embedding_model"]
        if engines["embedding_provider"] == "openai"
        else "hashing embedder"
    )
    return f"{agents} + {embedder}"


def _bullets(items: list[str], limit: int = 12) -> list[str]:
    shown = [f"- {item}" for item in items[:limit]]
    if len(items) > limit:
        shown.append(f"- … and {len(items) - limit} more (see the result file)")
    return [*shown, ""] if shown else ["- none", ""]


def _configuration(name: str, report: Json) -> list[str]:
    lines = [f"## Configuration: {_label(report)}", ""]
    run = report.get("run", {})
    lines += [
        f"Result file: `backend/evaluation/results/{name}.json`, run on "
        f"{report.get('generated_at', 'unknown date')}; "
        f"{sum(run.get('seconds', {}).values()):.0f} s wall time, "
        f"{run.get('llm_tokens', 0):,} LLM tokens.",
        "",
        "### Retrieval",
        "",
        "One relevant source per query; a source counts once however many of its passages "
        "are returned.",
        "",
    ]
    rows = []
    for mode, scores in report["retrieval"].items():
        for subset in ("all", "keyword", "paraphrase"):
            s = scores[subset]
            rows.append(
                [
                    mode,
                    f"{subset} ({s['queries']})",
                    _pct(s["recall_at_1"]),
                    _pct(s["recall_at_3"]),
                    _pct(s["recall_at_5"]),
                    f"{s['mrr']:.3f}",
                    f"{s['ndcg_at_5']:.3f}",
                ]
            )
    lines += _table(["Mode", "Queries", "Recall@1", "Recall@3", "Recall@5", "MRR", "nDCG@5"], rows)
    hybrid = report["retrieval"]["hybrid"]
    lines += [
        f"Hybrid queries whose source was not in the top 3 ({len(hybrid['not_in_top_3'])}):",
        "",
    ]
    lines += _bullets(hybrid["not_in_top_3"])

    ex = report["extraction"]
    lines += ["### Requirement extraction", ""]
    crit = ex["criteria"]
    lines += _table(
        ["Measure", "Result"],
        [
            [
                "Criteria precision",
                f"{_pct(crit['precision'])} ({crit['matched']} of {crit['predicted']} produced)",
            ],
            [
                "Criteria recall",
                f"{_pct(crit['recall'])} ({crit['matched']} of {crit['expected']} expected)",
            ],
            ["Criteria F1", f"{crit['f1']:.3f}"],
            [
                "Must-have vs nice-to-have",
                f"{_pct(ex['priority_accuracy'])} of {ex['priority_cases']} with a clear priority",
            ],
            ["Budget", f"{_pct(ex['budget_accuracy'])} of {ex['briefs']} briefs"],
            ["Category", f"{_pct(ex['category_accuracy'])} of {ex['briefs']} briefs"],
            ["Brands to avoid", _pct(ex["excluded_brand_accuracy"])],
        ],
    )
    lines += ["Briefs with a difference:", ""]
    lines += _bullets(
        [
            f"“{p['brief'][:70]}…”: missing {p['missing'] or 'nothing'}, "
            f"unexpected {p['unexpected'] or 'nothing'}"
            + ("" if p["budget_correct"] else ", budget wrong")
            for p in ex["problems"]
        ]
    )

    lines += ["### Product research (facts)", ""]
    lines += _research(report["research"])

    cp = report["comparison"]
    lines += ["### Comparison (who qualifies)", ""]
    lines += _table(
        ["Measure", "Result"],
        [
            [
                "Qualifies / ruled out decisions correct",
                f"{_pct(cp['decision_accuracy'])} of {cp['decisions']}",
            ],
            ["Scenarios whose winner truly qualifies", _pct(cp["winner_qualifies"])],
        ],
    )
    lines += _table(
        ["Scenario", "Should qualify", "Wrong decisions", "Winner"],
        [
            [
                s["scenario"],
                ", ".join(s["gold_qualifying"]),
                ", ".join(s["wrong_decisions"]) or "none",
                f"{s['winner'] or 'none'}{'' if s['winner_qualifies'] else ' (does not qualify)'}",
            ]
            for s in cp["scenarios"]
        ],
    )

    lines += ["### Ask (grounded answers)", ""]
    lines += _ask(report["ask"])
    lines += ["### Poisoned sources", ""]
    lines += _injection(report["injection"])

    held = report.get("held_out")
    if held:
        lines += [
            "### Held-out set",
            "",
            "Other product categories and other wording, written after the changes listed "
            "under “Changes made after the first run” and run once; nothing was changed "
            "after seeing these results.",
            "",
            "**Facts**",
            "",
            *_research(held["research"]),
            "**Ask**",
            "",
            *_ask(held["ask"]),
            "**Poisoned sources**",
            "",
            *_injection(held["injection"]),
        ]
    return lines


def _research(rs: Json) -> list[str]:
    lines = _table(
        ["Measure", "Result"],
        [
            ["Facts correct", f"{_pct(rs['accuracy'])} ({rs['correct']} of {rs['facts']})"],
            [
                "Stated in the documents and found",
                f"{rs['found']} of {rs['stated_in_documents']}",
            ],
            ["Correct when a value was found", _pct(rs["accuracy_when_found"])],
            [
                "Not stated, and nothing was invented",
                f"{rs['not_stated_and_nothing_invented']} of {rs['not_stated']}",
            ],
            ["Met / not met verdict correct", _pct(rs["requirement_status_accuracy"])],
            [
                "Cited facts whose citations are all from that product",
                f"{_pct(rs['citations_from_the_right_product'])} of {rs['cited_facts']}",
            ],
        ],
    )
    lines += _table(["Fact", "Accuracy"], [[k, _pct(v)] for k, v in rs["accuracy_by_fact"].items()])
    lines += ["Wrong or missing facts:", ""]
    lines += _bullets(
        [f"{e['product']} {e['fact']}: expected {e['gold']}, got {e['got']}" for e in rs["errors"]]
    )
    return lines


def _ask(ask: Json) -> list[str]:
    a, u, c = ask["answerable"], ask["unanswerable"], ask["citations"]
    lines = _table(
        ["Measure", "Result"],
        [
            [
                "Answerable questions answered correctly",
                f"{_pct(a['accuracy'])} ({a['correct']} of {a['total']})",
            ],
            ["Answered wrongly", str(a["wrong"])],
            ["Abstained although the sources answer", str(a["abstained"])],
            ["Correct when it did answer", _pct(a["accuracy_when_answered"])],
            [
                "Unanswerable questions correctly declined",
                f"{_pct(u['abstention_rate'])} ({u['abstained']} of {u['total']})",
            ],
            [
                "Cited passages from the product asked about",
                f"{_pct(c['from_the_right_product'])} of {c['cited_passages']}",
            ],
            ["Answers passing the citation validator", _pct(c["answers_passing_validation"])],
        ],
    )
    lines += ["Failures:", ""]
    lines += _bullets([f"{f['question']} ({f['reason']})" for f in ask["failures"]])
    return lines


def _injection(inj: Json) -> list[str]:
    n = inj["poisoned_documents"]
    return _table(
        ["Measure", "Result"],
        [
            ["Poisoned documents added", str(n)],
            [
                "Injected instructions echoed in any output",
                ", ".join(f"“{e}”" for e in inj["instruction_text_echoed"]) or "none",
            ],
            [
                "False claims adopted as researched facts",
                f"{inj['false_claims_adopted_by_research']} of {n}",
            ],
            [
                "False claims repeated in answers",
                f"{inj['false_claims_repeated_in_answers']} of {n}",
            ],
            [
                "Qualifies decisions changed by the poison",
                ", ".join(inj["qualifying_changed"]) or "none",
            ],
        ],
    )


def _changes(before: Json, after: Json) -> list[str]:
    """What the first run found, what was changed, and the same measures afterwards."""
    b_ask, a_ask = before["ask"], after["ask"]
    b_inj, a_inj = before["injection"], after["injection"]
    n = b_inj["poisoned_documents"]
    rows = [
        [
            "Answerable questions answered correctly",
            f"{b_ask['answerable']['correct']} of {b_ask['answerable']['total']}",
            f"{a_ask['answerable']['correct']} of {a_ask['answerable']['total']}",
        ],
        [
            "Answered wrongly",
            str(b_ask["answerable"]["wrong"]),
            str(a_ask["answerable"]["wrong"]),
        ],
        [
            "Abstained although the sources answer",
            str(b_ask["answerable"]["abstained"]),
            str(a_ask["answerable"]["abstained"]),
        ],
        [
            "Unanswerable questions correctly declined",
            f"{b_ask['unanswerable']['abstained']} of {b_ask['unanswerable']['total']}",
            f"{a_ask['unanswerable']['abstained']} of {a_ask['unanswerable']['total']}",
        ],
        [
            "Facts correct",
            f"{before['research']['correct']} of {before['research']['facts']}",
            f"{after['research']['correct']} of {after['research']['facts']}",
        ],
        [
            "False claims adopted as researched facts",
            f"{b_inj['false_claims_adopted_by_research']} of {n}",
            f"{a_inj['false_claims_adopted_by_research']} of {n}",
        ],
        [
            "False claims repeated in answers",
            f"{b_inj['false_claims_repeated_in_answers']} of {n}",
            f"{a_inj['false_claims_repeated_in_answers']} of {n}",
        ],
        [
            "Injected instruction fragments echoed",
            str(len(b_inj["instruction_text_echoed"])),
            str(len(a_inj["instruction_text_echoed"])),
        ],
        [
            "Qualifies decisions changed by the poison",
            str(len(b_inj["qualifying_changed"])),
            str(len(a_inj["qualifying_changed"])),
        ],
    ]
    return [
        "## Changes made after the first run (offline configuration)",
        "",
        "The first run exposed four defects. They were fixed, which means the main set's "
        "“after” numbers were obtained with knowledge of that set; the held-out set below "
        "is the unbiased check.",
        "",
        "1. **Questions that name a product were answered with document titles.** The "
        "answerer scored sentences by how many question words they contain, and a title "
        "that repeats the product's name scored highest. Now a named product limits the "
        "search to that product's sources and its name is left out of the scoring.",
        "2. **The offline engines read planted instructions.** The filter that withholds "
        "instruction-like sentences only ran for the language model. It now also runs "
        "before the rule-based agents and the offline answerer read evidence.",
        "3. **Any source could overrule a specification sheet.** Facts and prices are now "
        "read from documents of record (specification sheet, manufacturer page, manual, "
        "warranty) before reviews, and from reviews before notes; answers prefer them "
        "mildly.",
        "4. **Two quantities in one sentence were mixed up** (“16 GB of memory with a 256 "
        "GB SSD” gave 16 GB of storage). The reader now takes the number attached to the "
        "word, not the first one nearby.",
        "",
        *_table(["Measure (main set)", "First run", "After the changes"], rows),
        "First-run result: `backend/evaluation/baseline/rules-hashing.json`.",
        "",
        "### Known limits that remain",
        "",
        "- A plainly false statement in a low-trust source (“the battery lasts 90 hours”) "
        "is still quoted by the offline answerer when it matches the question best. The "
        "citation shows where it came from, but the answer is wrong.",
        "- The instruction filter is pattern-based and misses paraphrases; the held-out "
        "set shows one getting through.",
        "- The offline answerer abstains on questions worded differently from the source "
        "(“return window” vs “returns are accepted within 14 days”).",
        "- Numbers written as words (“one terabyte”) are not read, and budgets written as "
        "“500 dollars” instead of “$500” are missed by the offline brief reader.",
        "- Search with the offline engines is weak on paraphrased queries: full-text search "
        "needs the query's words to be in the document, and the hashing embedder only "
        "approximates meaning. The OpenAI embedder is the intended remedy; its numbers "
        "appear below only if that configuration has been run.",
        "",
    ]


def render(results: dict[str, Json], baselines: dict[str, Json] | None = None) -> str:
    first = next(iter(results.values()))
    d = first["dataset"]
    lines = [
        "# Evaluation",
        "",
        "Generated by `make eval` from `backend/evaluation/results/*.json`; do not edit by hand.",
        "",
        "## What is measured, and how far to trust it",
        "",
        "The evaluation drives the real application through its HTTP API: documents are "
        "uploaded, chunked and embedded, then searches, requirement extraction, the agent "
        "pipeline, comparisons and questions run exactly as they do for a user. Scores "
        "compare the output with gold labels written by hand.",
        "",
        f"- **The dataset is synthetic and small**: {d['products']} invented laptops with "
        f"{d['documents']} documents, {d['search_queries']} search queries, {d['briefs']} "
        f"briefs ({d['brief_criteria']} criteria), {d['facts']} facts, "
        f"{d['questions_answerable']} answerable and {d['questions_unanswerable']} "
        f"unanswerable questions, {d['scenarios']} comparison scenarios and "
        f"{d['poisoned_documents']} poisoned documents; plus a held-out set of "
        f"{d['held_out']['products']} other products ({d['held_out']['facts']} facts, "
        f"{d['held_out']['questions_answerable'] + d['held_out']['questions_unanswerable']} "
        f"questions, {d['held_out']['poisoned_documents']} poisoned documents). "
        "Percentages over so few cases move a lot with one case.",
        "- **Same author**: the labels and the system have the same author, so this is not "
        "an independent benchmark. The main set was written before the first run; what "
        "was changed afterwards is listed below with before and after numbers.",
        "- **Deliberately awkward phrasing**: some documents say “one terabyte”, “990 g” or "
        "“two year”, as real documents do.",
        "- **The offline configuration is deterministic**, and a test fails if its committed "
        "result no longer matches a fresh run. OpenAI results vary between runs.",
        "",
    ]
    offline = "rules-hashing"
    if baselines and offline in baselines and offline in results:
        lines += _changes(baselines[offline], results[offline])
    for name, report in results.items():
        lines += _configuration(name, report)
    lines += [
        "## Reproduce",
        "",
        "```bash",
        "make up                      # PostgreSQL",
        "make eval                    # offline engines; no API key, no cost",
        "make eval ENGINE=openai EMBEDDER=openai   # uses OPENAI_API_KEY from .env",
        "```",
        "",
        "Each run creates and drops its own database.",
    ]
    return "\n".join(lines) + "\n"
