"""Replayable labeling playbook for mailroom-dataset (v10 label completion and later).

Modules:

* ``registry`` loads the field registry, model registry, prompts and Dojo snapshot.
* ``tasks`` turns a dataset row into messages and a JSON schema for one labeling task.
* ``providers`` sends one request to Modal vLLM or OpenRouter (OpenAI-compatible).
* ``spend`` enforces the cost cap and key headroom and projects campaign cost.
* ``verify`` checks evidence, normalises values per Dojo type and compares two models.
* ``export`` writes the JSONL journal and the review CSVs.
* ``runner`` is the command line: ``python -m mailroom_eda.labeling --help``.

See ``README.md`` in this directory for the strategy.
"""
