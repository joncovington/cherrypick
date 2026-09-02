"""cherrypick-streamer — the suite's standalone DXLink market-data daemon.

Imports as ``cherrypick.streamer``, composing with ``cherrypick.core`` (and every other module's
``cherrypick.<module>``) under one ``cherrypick.*`` root. The parent ``src/cherrypick/`` directory
deliberately has **no** ``__init__.py`` — it is a PEP 420 native namespace, which is what lets
separately-installed distributions share the root. This file, one level down, is an ordinary package
marker and is required.

Not to be confused with ``cherrypick.core.streamer``: that is the shared *engine* (``ChainStreamer``);
this is the *daemon* that runs it — PID guard, ``--status``/``--stop``, logging, and the subscription
registry.
"""
