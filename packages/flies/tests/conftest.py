"""Put the cherrypick-core submodule on sys.path for the test session.

The module's own files bootstrap `src/_core` when imported (the suite-wide pattern — see MEIC's
paper.py), but that makes a test's `cherrypick.core.*` import depend on which of our modules happened
to be imported first. Import sorters reorder those freely, so doing it here once removes an ordering
trap that would otherwise surface as a collection error after an unrelated lint fix.
"""

import os
import sys

_CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)
