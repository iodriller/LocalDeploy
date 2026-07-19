"""Compatibility shim - the implementation lives in ``localdeploy.chat_cli``.

``sys.modules`` aliasing makes ``import chat_cli`` yield the *same* module object
as ``localdeploy.chat_cli``, so attribute access, monkeypatching, and ``from
chat_cli import X`` all behave exactly as before the module moved into the
package.
"""
import sys

from localdeploy import chat_cli as _mod

sys.modules[__name__] = _mod

if __name__ == "__main__":
    raise SystemExit(_mod.main())
