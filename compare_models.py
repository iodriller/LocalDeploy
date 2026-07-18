"""Compatibility shim — the implementation lives in ``localdeploy.compare_models``.

``sys.modules`` aliasing makes ``import compare_models`` yield the *same* module object
as ``localdeploy.compare_models``, so attribute access, monkeypatching, and ``from
compare_models import X`` all behave exactly as before the module moved into the
package.
"""
import sys

from localdeploy import compare_models as _mod

sys.modules[__name__] = _mod

if __name__ == "__main__":
    raise SystemExit(_mod.main())
