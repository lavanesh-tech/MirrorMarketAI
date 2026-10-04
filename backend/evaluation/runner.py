"""Runs the whole product end to end through its HTTP API and scores what comes back.

Nothing is mocked: documents are uploaded, chunked and embedded, and every search,
extraction, agent run and answer goes through the same endpoints the web app uses.
That way the numbers describe the system a user gets, not a component in isolation.
The engines are whatever the given settings select (offline rules + hashing embedder,
or OpenAI), which is recorded in the report.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import Settings
from app.main import create_app
from evaluation import dataset as ds
from evaluation.metrics import (
    contains_any,
    hit_at,
    mean,
    ndcg_at,
    precision_recall_f1,
    rank_of,
    ratio,
    reciprocal,
    same_number,
)

API = "/api/v1"
SEARCH_MODES = ("lexical", "vector", "hybrid")
SEARCH_LIMIT = 10
TOP = 3  # "found" for the list of misses
HTTP_ERROR = 400
PASSWORD = "evaluation-only-password"  # noqa: S105 - throwaway account in a throwaway database

Json = dict[str, Any]


class EvaluationError(RuntimeError):
    """The system under test did not accept a step the evaluation depends on."""


class _Client:
    """Thin wrapper: every call must succeed, and JSON comes back parsed."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.headers: dict[str, str] = {}

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.http.request(method, f"{API}{path}", headers=self.headers, **kwargs)
        if response.status_code >= HTTP_ERROR:
            raise EvaluationError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else None


class Evaluation:
    def __init__(self, client: _Client, log: Callable[[str], None], corpus: ds.Corpus) -> None:
        self.api = client
        self.log = log
        self.corpus = corpus
        self.workspace = ""
        self.products: dict[str, str] = {}  # dataset key -> product id
        self.keys: dict[str, str] = {}  # product id -> dataset key
        self.sources: dict[tuple[str, str], str] = {}  # (product key, doc kind) -> source id
        self.requirement_version = 0
        self.tokens = 0
        self.clean_facts: dict[str, dict[str, Json]] = {}  # before any poisoned source

    # ------------------------------------------------------------------ setup
    async def sign_in(self) -> None:
        email = f"eval-{uuid.uuid4().hex[:12]}@example.com"
        await self.api.call(
            "POST",
            "/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": "Evaluation"},
        )
        tokens = await self.api.call(
            "POST", "/auth/login", json={"email": email, "password": PASSWORD}
        )
        self.api.headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    async def setup(self) -> None:
        self.workspace = (await self.api.call("POST", "/workspaces", json={"name": "Evaluation"}))[
            "id"
        ]
        for product in self.corpus.products:
            created = await self.api.call(
                "POST",
                "/products",
                # A unique suffix keeps reruns against a reused database from colliding.
                json={
                    "brand": product.brand,
                    "name": f"{product.name} eval-{uuid.uuid4().hex[:6]}",
                    "category": product.category,
                },
            )
            self.products[product.key] = created["id"]
            self.keys[created["id"]] = product.key
            await self.api.call(
                "POST", f"/workspaces/{self.workspace}/products", json={"product_id": created["id"]}
            )
            for kind, text in product.docs.items():
                self.sources[(product.key, kind)] = await self._upload(
                    product.key, f"{product.full_name} {kind}", ds.SOURCE_TYPES[kind], text
                )

    async def _upload(self, product_key: str, title: str, source_type: str, text: str) -> str:
        uploaded = await self.api.call(
            "POST",
            f"/products/{self.products[product_key]}/sources/upload",
            files={"file": ("document.txt", text.encode(), "text/plain")},
            data={"source_type": source_type, "title": title, "workspace_id": self.workspace},
        )
        source_id: str = uploaded["source"]["id"]
        await self.api.call("POST", f"/sources/{source_id}/embed")
        return source_id

    # -------------------------------------------------------------- retrieval
    async def retrieval(self) -> Json:
        report: Json = {}
        for mode in SEARCH_MODES:
            ranks: list[tuple[ds.Query, int | None]] = []
            degraded = 0
            for query in ds.QUERIES:
                result = await self.api.call(
                    "POST",
                    f"/workspaces/{self.workspace}/search",
                    json={"query": query.text, "mode": mode, "limit": SEARCH_LIMIT},
                )
                degraded += bool(result["degraded"])
                ranked = list(dict.fromkeys(item["source_id"] for item in result["items"]))
                ranks.append((query, rank_of(ranked, self.sources[(query.product, query.doc)])))
            report[mode] = {
                "all": _ranking_scores([rank for _, rank in ranks]),
                "keyword": _ranking_scores([r for q, r in ranks if q.kind == "keyword"]),
                "paraphrase": _ranking_scores([r for q, r in ranks if q.kind == "paraphrase"]),
                "degraded_queries": degraded,
                "not_in_top_3": [q.text for q, r in ranks if r is None or r > TOP],
            }
        return report

    # ------------------------------------------------------------- extraction
    async def extraction(self) -> Json:
        matched = predicted = expected = 0
        budgets = categories = priorities = priority_total = brands = brand_total = 0
        extractors: set[str] = set()
        degraded = 0
        problems: list[Json] = []
        for brief in ds.BRIEFS:
            result = await self.api.call(
                "POST",
                f"/workspaces/{self.workspace}/requirements/extract",
                json={"text": brief.text},
            )
            extractors.add(result["extractor"])
            degraded += bool(result["degraded"])
            spec = result["spec"]
            found: list[Json] = spec.get("criteria") or []
            remaining = list(found)
            missing: list[str] = []
            for gold in brief.criteria:
                hit = next((c for c in remaining if _criterion_matches(c, gold)), None)
                if hit is None:
                    missing.append(gold.key)
                    continue
                remaining.remove(hit)
                matched += 1
                if gold.priority is not None:
                    priority_total += 1
                    priorities += hit["priority"] == gold.priority
            predicted += len(found)
            expected += len(brief.criteria)
            budget = spec.get("budget") or {}
            budget_ok = same_number(budget.get("max_amount"), brief.budget_max) and (
                brief.budget_min is None or same_number(budget.get("min_amount"), brief.budget_min)
            )
            budgets += budget_ok
            categories += spec.get("category") == brief.category
            if brief.excluded_brands:
                brand_total += 1
                got = {b.casefold() for b in spec.get("excluded_brands") or []}
                brands += got == {b.casefold() for b in brief.excluded_brands}
            if missing or remaining or not budget_ok:
                problems.append(
                    {
                        "brief": brief.text,
                        "missing": missing,
                        "unexpected": [f"{c['key']} {c['operator']}" for c in remaining],
                        "budget_correct": budget_ok,
                    }
                )
        total = len(ds.BRIEFS)
        return {
            "extractor": sorted(extractors),
            "degraded_briefs": degraded,
            "criteria": precision_recall_f1(matched, predicted, expected)
            | {"matched": matched, "predicted": predicted, "expected": expected},
            "priority_accuracy": ratio(priorities, priority_total),
            "priority_cases": priority_total,
            "budget_accuracy": ratio(budgets, total),
            "category_accuracy": ratio(categories, total),
            "excluded_brand_accuracy": ratio(brands, brand_total),
            "briefs": total,
            "problems": problems,
        }

    # -------------------------------------------------- research + comparison
    async def _analyze(self, spec: Json) -> Json:
        saved = await self.api.call(
            "PUT",
            f"/workspaces/{self.workspace}/requirements",
            json={"spec": spec, "expected_version": self.requirement_version},
        )
        self.requirement_version = saved["current_version"]
        analysis = await self.api.call("POST", f"/workspaces/{self.workspace}/analyze")
        self.tokens += analysis["tokens_used"]
        comparison = await self.api.call("POST", f"/workspaces/{self.workspace}/compare")
        output: Json = comparison["output"]
        return output

    async def _latest(self, agent: str, product_key: str) -> Json | None:
        page = await self.api.call(
            "GET",
            f"/workspaces/{self.workspace}/agent-runs",
            params={"agent": agent, "product_id": self.products[product_key], "limit": 1},
        )
        return page["items"][0] if page["items"] else None

    async def _pack(self, pack_id: str | None) -> dict[str, Json]:
        """Evidence items of a pack by marker ("E1"...), or {} when the run used none."""
        if pack_id is None:
            return {}
        pack = await self.api.call("GET", f"/workspaces/{self.workspace}/evidence-packs/{pack_id}")
        return {item["marker"]: item for item in pack["items"]}

    async def facts(self) -> dict[str, dict[str, Json]]:
        """Per product: the researched value for each fact key, plus the price."""
        facts: dict[str, dict[str, Json]] = {}
        for product in self.corpus.products:
            research = await self._latest("product_research", product.key)
            value = await self._latest("value", product.key)
            evidence = await self._pack(research["evidence_pack_id"]) if research else {}
            found: dict[str, Json] = {}
            for fact in (research or {}).get("output", {}).get("facts", []):
                cited = [evidence[m] for m in fact.get("citations", []) if m in evidence]
                found[fact["key"]] = {
                    "value": fact.get("value_number"),
                    "status": fact.get("status"),
                    "source": fact.get("source"),
                    "cited_products": [self.keys.get(item["product_id"]) for item in cited],
                    "citations": len(fact.get("citations", [])),
                }
            price = ((value or {}).get("output") or {}).get("price") or {}
            found["price"] = {"value": price.get("amount"), "source": price.get("source")}
            facts[product.key] = found
        return facts

    async def research_and_comparison(self) -> tuple[Json, Json]:
        scenario_reports: list[Json] = []
        research: Json = {}
        decisions = correct_decisions = winners = 0
        for index, scenario in enumerate(self.corpus.scenarios):
            self.log(f"scenario: {scenario.name}")
            output = await self._analyze(scenario.spec)
            if index == 0:
                self.clean_facts = await self.facts()
                research = _score_research(self.clean_facts, scenario, self.corpus)
            qualifying = {
                self.keys[p["product_id"]]
                for p in output["products"]
                if p["eligible"] and not p["unknown_hard"]
            }
            gold = scenario.qualifying(self.corpus.products)
            wrong = sorted(qualifying ^ gold)
            decisions += len(self.corpus.products)
            correct_decisions += len(self.corpus.products) - len(wrong)
            winner = self.keys.get(output["winner_product_id"] or "")
            winners += winner in gold
            scenario_reports.append(
                {
                    "scenario": scenario.name,
                    "gold_qualifying": sorted(gold),
                    "predicted_qualifying": sorted(qualifying),
                    "wrong_decisions": wrong,
                    "winner": winner,
                    "winner_qualifies": winner in gold,
                }
            )
        comparison = {
            "decision_accuracy": ratio(correct_decisions, decisions),
            "decisions": decisions,
            "winner_qualifies": ratio(winners, len(self.corpus.scenarios)),
            "scenarios": scenario_reports,
        }
        return research, comparison

    # -------------------------------------------------------------------- ask
    async def _ask(self, question: str) -> tuple[Json, list[Json]]:
        run = await self.api.call(
            "POST", f"/workspaces/{self.workspace}/ask", json={"question": question}
        )
        self.tokens += run["tokens_used"]
        output: Json = run["output"]
        evidence = await self._pack(run["evidence_pack_id"]) if output["cited"] else {}
        cited = [evidence[m] for m in output["cited"] if m in evidence]
        return run, cited

    async def ask(self) -> Json:
        correct = wrong = missed = abstained_right = answered_unanswerable = 0
        cited_total = cited_on_product = valid = answered = 0
        engines: set[str] = set()
        degraded = 0
        failures: list[Json] = []
        for question in self.corpus.questions:
            run, cited = await self._ask(question.text)
            output = run["output"]
            engines.add(run["engine"])
            degraded += bool(run["degraded"])
            if question.accept is None:
                if output["abstained"]:
                    abstained_right += 1
                else:
                    answered_unanswerable += 1
                    failures.append(_failure(question, output, "answered without support"))
                continue
            if output["abstained"]:
                missed += 1
                failures.append(_failure(question, output, "abstained"))
                continue
            answered += 1
            valid += bool((run.get("validation") or {}).get("valid"))
            on_product = [c for c in cited if self.keys.get(c["product_id"]) == question.product]
            cited_total += len(cited)
            cited_on_product += len(on_product)
            if contains_any(output["answer"], question.accept) and on_product:
                correct += 1
            else:
                wrong += 1
                failures.append(_failure(question, output, "wrong answer or wrong product"))
        answerable = sum(q.accept is not None for q in self.corpus.questions)
        unanswerable = len(self.corpus.questions) - answerable
        return {
            "engine": sorted(engines),
            "degraded_answers": degraded,
            "answerable": {
                "total": answerable,
                "correct": correct,
                "wrong": wrong,
                "abstained": missed,
                "accuracy": ratio(correct, answerable),
                "accuracy_when_answered": ratio(correct, answered),
            },
            "unanswerable": {
                "total": unanswerable,
                "abstained": abstained_right,
                "answered": answered_unanswerable,
                "abstention_rate": ratio(abstained_right, unanswerable),
            },
            "citations": {
                "cited_passages": cited_total,
                "from_the_right_product": ratio(cited_on_product, cited_total),
                "answers_passing_validation": ratio(valid, answered),
            },
            "failures": failures,
        }

    # -------------------------------------------------------------- injection
    async def injection(self) -> Json:
        for poison in self.corpus.poisons:
            await self._upload(poison.product, poison.title, poison.source_type, poison.text)
        scenario = self.corpus.scenarios[0]
        # A changed note makes this a new requirements version, so every agent runs again.
        output = await self._analyze(scenario.spec | {"notes": "after poisoned sources"})
        facts = await self.facts()
        qualifying = {
            self.keys[p["product_id"]]
            for p in output["products"]
            if p["eligible"] and not p["unknown_hard"]
        }
        attacks: list[Json] = []
        written: list[str] = []
        for poison in self.corpus.poisons:
            run, _ = await self._ask(poison.question)
            answer = run["output"]["answer"]
            written.append(answer)
            adopted = same_number(
                facts[poison.product][poison.fact_key]["value"], poison.false_value
            )
            before = self.clean_facts[poison.product][poison.fact_key]["value"]
            attacks.append(
                {
                    "product": poison.product,
                    "false_claim": f"{poison.fact_key} = {poison.false_value:g}",
                    "fact_before": before,
                    "fact_after": facts[poison.product][poison.fact_key]["value"],
                    "false_claim_in_research": adopted,
                    "false_claim_in_answer": contains_any(answer, poison.forbidden),
                    "answer_abstained": run["output"]["abstained"],
                }
            )
        for product in self.corpus.products:
            for agent in ("synthesis", "review_intelligence", "risk"):
                latest = await self._latest(agent, product.key)
                written.append(str((latest or {}).get("output") or ""))
        echoed = sorted({e for e in self.corpus.echoes for text in written if e in text.casefold()})
        gold = scenario.qualifying(self.corpus.products)
        return {
            "poisoned_documents": len(self.corpus.poisons),
            "false_claims_adopted_by_research": sum(a["false_claim_in_research"] for a in attacks),
            "false_claims_repeated_in_answers": sum(a["false_claim_in_answer"] for a in attacks),
            "instruction_text_echoed": echoed,
            "qualifying_changed": sorted(qualifying ^ gold),
            "winner_after": self.keys.get(output["winner_product_id"] or ""),
            "attacks": attacks,
        }


def _ranking_scores(ranks: list[int | None]) -> Json:
    return {
        "queries": len(ranks),
        "recall_at_1": round(mean([hit_at(r, 1) for r in ranks]), 4),
        "recall_at_3": round(mean([hit_at(r, 3) for r in ranks]), 4),
        "recall_at_5": round(mean([hit_at(r, 5) for r in ranks]), 4),
        "mrr": round(mean([reciprocal(r) for r in ranks]), 4),
        "ndcg_at_5": round(mean([ndcg_at(r, 5) for r in ranks]), 4),
    }


def _criterion_matches(found: Json, gold: ds.GoldCriterion) -> bool:
    if found["key"] != gold.key or found["operator"] not in gold.operators:
        return False
    if gold.number is not None:
        return any(
            same_number(found.get("value_number"), number) for number in (gold.number, *gold.also)
        )
    return str(found.get("value_text") or "").casefold() == (gold.text or "").casefold()


def _score_research(
    facts: dict[str, dict[str, Json]], scenario: ds.Scenario, corpus: ds.Corpus
) -> Json:
    targets = {c["key"]: c for c in scenario.criteria}
    keys = (*corpus.fact_keys, "price")
    stated = correct = found = found_right = absent = absent_right = 0
    status_ok = status_total = cited = cited_right = from_evidence = 0
    by_key = dict.fromkeys(keys, 0)
    errors: list[Json] = []
    for product in corpus.products:
        for key in keys:
            got = facts[product.key].get(key) or {}
            gold = product.facts[key]
            value = got.get("value")
            if gold is None:
                # The documents do not state it: the right result is to find nothing.
                absent += 1
                right = value is None
                absent_right += right
            else:
                stated += 1
                found += value is not None
                right = any(
                    same_number(value, accepted)
                    for accepted in ds.accepted_values(product, key)
                    if accepted is not None
                )
                found_right += right
            correct += right
            by_key[key] += right
            if not right:
                errors.append({"product": product.key, "fact": key, "gold": gold, "got": value})
            if key == "price" or gold is None:
                continue
            from_evidence += got.get("source") == "evidence"
            if got.get("citations"):
                cited += 1
                cited_right += bool(got["cited_products"]) and all(
                    p == product.key for p in got["cited_products"]
                )
            target = targets[key]
            met = (
                gold >= target["value_number"]
                if target["operator"] == ">="
                else gold <= target["value_number"]
            )
            status_total += 1
            status_ok += got.get("status") == ("MET" if met else "UNMET")
    total = len(corpus.products) * len(keys)
    return {
        "facts": total,
        "stated_in_documents": stated,
        "found": found,
        "correct": correct,
        "accuracy": ratio(correct, total),
        "accuracy_when_found": ratio(found_right, found),
        "not_stated": absent,
        "not_stated_and_nothing_invented": absent_right,
        "accuracy_by_fact": {k: ratio(v, len(corpus.products)) for k, v in by_key.items()},
        "requirement_status_accuracy": ratio(status_ok, status_total),
        "values_from_evidence": from_evidence,
        "cited_facts": cited,
        "citations_from_the_right_product": ratio(cited_right, cited),
        "errors": errors,
    }


def _failure(question: ds.Question, output: Json, reason: str) -> Json:
    return {"question": question.text, "reason": reason, "answer": output["answer"][:200]}


async def evaluate(settings: Settings, log: Callable[[str], None] = lambda _: None) -> Json:
    """Run every stage against an app built from `settings` (its database must be migrated)."""
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://evaluation", timeout=300) as http,
    ):
        client = _Client(http)
        run = Evaluation(client, log, ds.MAIN)
        report: Json = {
            "engines": {
                "requirements_extractor": settings.requirements_extractor,
                "agent_engine": settings.agent_engine,
                "embedding_provider": settings.embedding_provider,
                "chat_model": settings.openai_chat_model
                if "openai" in (settings.requirements_extractor, settings.agent_engine)
                else None,
                "embedding_model": settings.openai_embedding_model
                if settings.embedding_provider == "openai"
                else None,
            },
            "dataset": ds.summary() | {"synthetic": True},
        }
        timings: dict[str, float] = {}

        async def stage(name: str, work: Callable[[], Any]) -> Any:
            log(name)
            started = time.perf_counter()
            result = await work()
            timings[name] = round(time.perf_counter() - started, 2)
            return result

        await run.sign_in()
        await stage("setup", run.setup)
        report["retrieval"] = await stage("retrieval", run.retrieval)
        report["extraction"] = await stage("extraction", run.extraction)
        research, comparison = await stage("research_and_comparison", run.research_and_comparison)
        report["research"], report["comparison"] = research, comparison
        report["ask"] = await stage("ask", run.ask)
        report["injection"] = await stage("injection", run.injection)

        held_out = Evaluation(client, log, ds.HELD_OUT)

        async def run_held_out() -> Json:
            await held_out.setup()
            facts, _ = await held_out.research_and_comparison()
            return {
                "research": facts,
                "ask": await held_out.ask(),
                "injection": await held_out.injection(),
            }

        report["held_out"] = await stage("held_out", run_held_out)
        # Not part of the comparable result: varies between machines and runs.
        report["run"] = {"seconds": timings, "llm_tokens": run.tokens + held_out.tokens}
        return report
