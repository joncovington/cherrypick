"""`cherrypick.ensemble`, the old name of `cherrypick.automations`, kept resolving.

`import cherrypick.ensemble.engine` returns the module `cherrypick.automations.engine` itself (one module,
one state, not a second copy), and `python -m cherrypick.ensemble.paper_loop` runs the new module's code.
The finished study scripts and a machine's suite config written before the rename name these paths.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

import cherrypick.automations as _new

OLD, NEW = "cherrypick.ensemble", "cherrypick.automations"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self):
        self._specs: dict = {}

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(OLD + "."):
            return None
        new = NEW + fullname[len(OLD) :]
        real = importlib.util.find_spec(new)
        if real is None:
            return None
        return importlib.util.spec_from_loader(
            fullname, self, is_package=real.submodule_search_locations is not None
        )

    def create_module(self, spec):
        module = importlib.import_module(NEW + spec.name[len(OLD) :])
        self._specs[spec.name] = module.__spec__
        return module

    def exec_module(self, module):
        # Already run under its new name. The import machinery stamped the alias's spec on it: put its
        # own back, so the module still says what it is.
        module.__spec__ = self._specs.pop(module.__spec__.name, module.__spec__)

    def get_code(self, fullname):
        """For `python -m`: the new module's code, run as `__main__`."""
        new = NEW + fullname[len(OLD) :]
        return importlib.util.find_spec(new).loader.get_code(new)


if not any(isinstance(f, _Alias) for f in sys.meta_path):
    sys.meta_path.insert(0, _Alias())

__path__ = []  # a package, so `cherrypick.ensemble.<x>` is looked up; the finder answers it
__doc__ = (__doc__ or "") + "\n" + (_new.__doc__ or "")
