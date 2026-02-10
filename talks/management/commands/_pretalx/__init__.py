"""
Private helpers for the ``import_pretalx_talks`` management command.

This package splits the large monolithic command into focused modules:

* **types** - shared enums and tiny Pydantic models (``VerbosityLevel``, ``PytanisCfg``).
* **submission** - ``SubmissionData`` extraction and submission-level classification.
* **avatars** - on-disk + in-memory avatar cache and async prefetch.
* **images** - social-card generation (Pillow / Pilmoji).
* **speakers** - batch speaker create/update helpers.
* **rooms** - batch room create helpers.
"""
