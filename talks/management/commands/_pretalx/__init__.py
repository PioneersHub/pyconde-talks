"""
Private helpers for the ``import_pretalx_talks`` management command.

This package splits the large monolithic command into focused modules:

* **avatars** - on-disk + in-memory avatar cache and async prefetch.
* **client** - Pretalx API client (``PretalxClient``), setup, and data fetching with retry.
* **context** - ``ImportContext`` frozen dataclass (typed Parameter Object).
* **events** - event resolution, creation, and name synchronization.
* **images** - social-card generation (Pillow / Pilmoji).
* **mixins** - ``LoggingMixin`` and ``ProcessingMixin`` for the Command class.
* **pretalx_models** - typed Pydantic models for the Pretalx API responses we read.
* **rooms** - single and batch room creation helpers.
* **speakers** - single and batch speaker create/update helpers.
* **submission** - ``SubmissionData`` extraction and submission-level classification.
* **talks** - talk CRUD, presentation-type mapping, and per-talk speaker wiring.
* **types** - shared enums and protocols (``VerbosityLevel``, ``LogFn``).
* **validation** - submission validation for import eligibility.
"""
