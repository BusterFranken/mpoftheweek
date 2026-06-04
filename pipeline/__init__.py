"""MEP of the Week data pipeline.

Fetches official European Parliament data (declared lobby meetings, the
current-MEP register and shadow-rapporteur assignments), normalizes it and
writes deterministic JSON build inputs for the static site under
``site/src/data/``.

Run the full refresh with::

    python -m pipeline.run
"""
