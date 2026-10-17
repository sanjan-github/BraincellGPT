# BrainCell Benchmark

This directory defines initial behavioral test cases. They are test specifications, not measured results.

## Planned metrics

- Context-dependent answer accuracy
- Ambiguity handling rate
- Contradiction resolution accuracy
- Tool completion honesty
- Memory retrieval precision and recall
- Median and p95 latency
- Estimated cost per conversation

## Method

1. Run each case with the baseline context window.
2. Run the same cases with persistent memory enabled after that feature exists.
3. Compare exact checks where deterministic and use a documented rubric for qualitative judgments.
4. Keep model name, prompt version, test dataset version, and timestamp with each result.
5. Report failures as well as aggregate scores. Do not label test specifications as completed benchmarks.
