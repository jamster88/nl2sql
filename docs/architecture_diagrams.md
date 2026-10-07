# Architecture diagrams

*Part of the [nl2sql documentation](../README.md#documentation).*

[`arch_diagrams/`](../arch_diagrams) holds one diagram per agent version: what
each step does, why it is there, and how control flows.

| | |
|---|---|
| [`arch_v1.svg`](../arch_diagrams/arch_v1.svg) | The schema-only pipeline |
| [`arch_v2.svg`](../arch_diagrams/arch_v2.svg) | The same pipeline with retrieval in front of it, and that context threaded into three of the five steps |
| [`arch_v3.svg`](../arch_diagrams/arch_v3.svg) | Both retrieval steps, the three-retriever ensemble behind the second, and the two data-flow rails they feed |
| [`arch_v4.svg`](../arch_diagrams/arch_v4.svg) | The multi-agent pipeline: four stages, the parallel retrievers, the deterministic gates, the repair loop, and the presentation trio |
| [`arch_v5.svg`](../arch_diagrams/arch_v5.svg) | v4 plus the answer contract and the Completeness Reviewer inside the repair loop |
| [`arch_v5_1.svg`](../arch_diagrams/arch_v5_1.svg) | The v5 pipeline unchanged, with the review side added to the deployment: one pane per verdict, the golden set, and the corrections and completions stores |
| [`arch_v5_2.svg`](../arch_diagrams/arch_v5_2.svg) | v5.1 with every model call routed: the catalog and the routed chat models in the deployment, the router and the ladder, and on each step that calls a model, the rung it is routed at |
| [`arch_v5_6.svg`](../arch_diagrams/arch_v5_6.svg) | v5.2 with the Snippet Retriever as a fifth Stage 1 branch, the snippet store and its document in the deployment, and the curation interface beside the review one |

All of them are laid out identically so the versions can be read side by side --
everything new or changed is marked, in teal for v2's retrieval and indigo for
v3's examples. Each shows the deployment (what runs where), the startup
preflight, every LangGraph node paired with the reasoning behind it, the retry
loop, and the exit codes.

They are generated, not drawn:

```bash
python arch_diagrams/generate.py
```

[`generate.py`](../arch_diagrams/generate.py) computes the layout -- text wrapped
against real font metrics, row heights following their content -- and records
the nodes it drew in the SVG, which is what lets
[`tests/docs/test_arch_diagrams.py`](../tests/docs/test_arch_diagrams.py) check
the pictures against `graph.py` and fail when a node is renamed. Edit the
content in the `build_v*()` functions and re-run; do not hand-edit the SVGs.

The adversarial reviews in [`adversary_reviews/`](../adversary_reviews) are
illustrated the same way:
[`adversary_reviews/diagrams/generate.py`](../adversary_reviews/diagrams/generate.py)
writes their nineteen figures as draw.io files -- ten for the first cycle
(`v6_x_review`, at 5.6.1) and nine for the second (`v6_2_review`, at 6.0.1),
a first-cycle figure never edited for the second -- draw.io's own command
line exports the SVG and PNG beside each (the commands are in the script's
docstring), and
[`tests/docs/test_review_diagrams.py`](../tests/docs/test_review_diagrams.py)
holds the committed files to the script, the exports to the files, the
`_enhanced` editions of the reviews to the originals they add figures to,
and the second cycle's comparison document to the finding ids both cycles
use.
