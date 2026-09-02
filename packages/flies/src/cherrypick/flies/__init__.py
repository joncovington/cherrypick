"""cherrypick-flies — 0DTE net-credit butterflies ("the profit forest").

Imports as ``cherrypick.flies``, composing with ``cherrypick.core`` (and every other module's
``cherrypick.<module>``) under one ``cherrypick.*`` root. The parent ``src/cherrypick/`` directory
deliberately has **no** ``__init__.py`` — it is a PEP 420 native namespace, which is what lets
separately-installed distributions share the root. This file, one level down, is an ordinary package
marker and is required.

Paper by default; ``cherrypick.flies.live_loop`` is the deliberately narrow, per-day-armed live pilot.
The measurement discipline this module exists for — floors after fees, the completion rate, the
uncompleted branch reported separately — lives in ``fly``/``engine``/``analytics``; see CLAUDE.md's
"honesty rules".
"""
