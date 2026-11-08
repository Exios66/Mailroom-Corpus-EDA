"""Replayable labeling playbook for mailroom-dataset (v10 label completion and later).

Modules:

* ``registry`` loads the field registry, model registry, prompts and Dojo snapshot.
* ``tasks`` turns a dataset row into messages and a JSON schema for one labeling task.
* ``providers`` sends one request to Modal vLLM or OpenRouter (OpenAI-compatible).
* ``spend`` enforces the cost cap and key headroom and projects campaign cost.
* ``verify`` checks evidence, normalises values per Dojo type and compares two models.
* ``scoring`` adds labeling-quality checks to the pinned Dojo catalog, not its metrics.
* ``export`` writes the JSONL journal and the review CSVs.
* ``runner`` is the command line: ``python -m mailroom_eda.labeling --help``.
* ``smoke_fixtures`` records, checks and replays the gold smoke fixtures under
  ``tests/fixtures/live_smoke/``: ``python -m mailroom_eda.labeling.smoke_fixtures --help``.

See ``README.md`` in this directory for the strategy.
"""
