"""The paraphrase set: three rewordings of each benchmark question, written by hand.

Phase 0 of arch7 (`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7.md`
section 10, and the implementation specification's section 8). The
ensemble rests on a premise -- that the pipeline's answer depends on how a
question is worded, often enough that asking it several ways and comparing
the answers is worth what it costs -- and these sixty wordings measure the
premise before anything is built on it.
`python benchmarks/run_benchmark.py --paraphrase-set` asks every question
as the benchmark asks it and as each of its rewordings asks it, scores
every answer against the question's own reference query -- a rewording
asks the same question, so it has the same answer -- and reports the
**stability**: the fraction of questions every wording of which came out
right.

Each rewording follows the rules arch7 section 22.3 gives the Paraphraser:
every number, year, name and quoted value kept as written; the same
entities, measure, period, direction and row count; no period, filter or
measure added and none dropped; the words and the shape varied -- a
question or an instruction, another verb, another order of clauses,
another register, "our" or "the company's". Each was checked by hand
against its question's reference SQL, and
`tests/benchmarks/test_paraphrases.py` holds each one to the fidelity
gate's checks in code (`nl2sql_agent.fidelity`: F1 numbers, F2 literals,
F3 polarity, F5 distinct) against the original, with the rewordings before
it kept -- so a rewording a person judged faithful and the checks would
discard is a failing test, which is how the checks' word lists grow.
"""

from __future__ import annotations

from benchmarks.questions import BenchmarkQuestion

PARAPHRASES: dict[str, tuple[str, str, str]] = {
    "B01": (
        "What is the total number of stores?",
        "Give me a count of all our stores.",
        "How many stores does the company have?",
    ),
    "B02": (
        "What is the total number of products we carry, and how many of those are private label?",
        "Give the number of products we stock and the number of those that are private label.",
        "Of all the products the company carries, how many are private label, and how many products are "
        "there in total?",
    ),
    "B03": (
        "For each banner, what is the number of stores it operates?",
        "Give the store count per banner.",
        "How many stores are run under each of the company's banners?",
    ),
    "B04": (
        "How much did our net sales come to in total for fiscal year 2025?",
        "Report the company's total net sales for fiscal year 2025.",
        "In FY2025, what was the total of our net sales?",
    ),
    "B05": (
        "In fiscal year 2025, which department brought in the most net sales, and how much was that?",
        "Name the department with the highest net sales for fiscal year 2025, along with the amount.",
        "What was the company's top department by net sales in FY2025, and what were its net sales?",
    ),
    "B06": (
        "How much markdown discount did we give in total during fiscal quarter 4 of fiscal year 2025?",
        "Report the total markdown discounts for fiscal Q4 of FY2025.",
        "How large were the total markdown discounts in the fourth fiscal quarter of fiscal year 2025?",
    ),
    "B07": (
        "For the Dairy & Eggs department, how much gross margin did we earn, as a percentage, in fiscal "
        "month 12 of fiscal year 2025?",
        "Calculate the gross margin percentage of the Dairy & Eggs department for fiscal month 12 of FY2025.",
        "What gross margin percentage did Dairy & Eggs achieve in the twelfth fiscal month of fiscal year 2025?",
    ),
    "B08": (
        "Across every product we track, what percentage of the market did we hold overall in fiscal year 2024?",
        "Give the company's overall market share as a percentage for fiscal year 2024, across all the "
        "products it tracks.",
        "In FY2024, what was our total market share across all tracked products, in percent?",
    ),
    "B09": (
        "For fiscal year 2025, what did we collect in vendor allowances for each allowance type? Identify "
        "each type by its short code, such as SCAN_BACK.",
        "Break down the vendor allowances the company collected in fiscal year 2025 by allowance type, "
        "reporting each type by its short code (for example SCAN_BACK).",
        "List the total vendor allowances we collected in fiscal year 2025 per allowance type, naming each "
        "type by its short code, like SCAN_BACK.",
    ),
    "B10": (
        "List the top 5 products by net sales in fiscal year 2025, giving the SKU and net sales amount for each.",
        "In fiscal year 2025, what were the 5 best-selling products by net sales? Show each product's SKU "
        "and its net sales.",
        "Give the SKU and net sales of the 5 products with the highest net sales in FY2025.",
    ),
    "B11": (
        "For each store banner, how much was the average basket worth in fiscal year 2025?",
        "Calculate the average basket value per store banner for FY2025.",
        "In fiscal year 2025, what did an average shopping basket come to at each of the company's store "
        "banners?",
    ),
    "B12": (
        "By promotion mechanic type, how many promotional units did we sell and how much incremental lift "
        "did we generate in fiscal year 2025?",
        "Show the promotional units sold and the incremental lift generated for each promotion mechanic type "
        "in fiscal year 2025.",
        "In FY2025, what were the promotional units sold and incremental lift for every promotion mechanic type?",
    ),
    "B13": (
        "On average, which competitor's prices are lowest compared with ours, and by how much?",
        "Name the competitor that prices lowest relative to the company on average, and say by how much.",
        "Which competitor is cheapest relative to our prices on average, and by how much?",
    ),
    "B14": (
        "Break down our fiscal year 2025 net sales by state.",
        "For each state, what were the company's net sales in fiscal year 2025?",
        "How much did we sell, in net sales, in each state during FY2025?",
    ),
    "B15": (
        "For each advertising channel type (Print Flyer, Paid Social and the rest), how many ad impressions "
        "and clicks were generated in fiscal year 2025?",
        "Total the ad impressions and clicks by advertising channel type -- such as Print Flyer or Paid "
        "Social -- for fiscal year 2025.",
        "In FY2025, what were the ad impressions and clicks for every type of advertising channel, like Print "
        "Flyer and Paid Social?",
    ),
}


def wordings(question: BenchmarkQuestion) -> tuple[str, ...]:
    """Every way the paraphrase set asks a question: the benchmark's own
    first -- wording 0, as the original is candidate 0 in the ensemble --
    then its three rewordings, numbered from 1."""
    return (question.question, *PARAPHRASES[question.id])
