"""Compatibility shim - the implementation lives in ``localdeploy.benchmark``.

``sys.modules`` aliasing makes ``import benchmark`` yield the *same* module object
as ``localdeploy.benchmark``, so attribute access, monkeypatching, and ``from
benchmark import X`` all behave exactly as before the module moved into the
package.
"""
import sys

from localdeploy import benchmark as _mod

sys.modules[__name__] = _mod

if __name__ == "__main__":
    raise SystemExit(_mod.main())
