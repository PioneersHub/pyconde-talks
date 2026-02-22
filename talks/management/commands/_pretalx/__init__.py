"""
Private helpers for the ``import_pretalx_talks`` management command.

This package splits the large monolithic command into focused modules:

* **avatars** - on-disk + in-memory avatar cache and async prefetch.
* **client** - Pretalx API client setup and data fetching with retry.
* **context** - ``ImportContext`` frozen dataclass (typed Parameter Object).
* **events** - event resolution, creation, and name synchronization.
* **images** - social-card generation (Pillow / Pilmoji).
* **mixins** - ``LoggingMixin``, ``FetchMixin``, and ``ProcessingMixin`` for the Command class.
* **rooms** - single and batch room creation helpers.
* **speakers** - single and batch speaker create/update helpers.
* **submission** - ``SubmissionData`` extraction and submission-level classification.
* **talks** - talk CRUD, presentation-type mapping, and per-talk speaker wiring.
* **types** - shared enums and tiny Pydantic models (``VerbosityLevel``, ``PytanisCfg``).
* **validation** - submission validation for import eligibility.
"""
