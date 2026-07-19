"""Compatibility shim - the implementation lives in ``localdeploy.server``.

``sys.modules`` aliasing makes ``import api_server`` yield the *same* module object
as ``localdeploy.server``, so attribute access, monkeypatching, and ``from
api_server import X`` all behave exactly as before the module moved into the
package. Run directly (``python api_server.py``) to serve the API.
"""
import sys

from localdeploy import server as _mod

sys.modules[__name__] = _mod

if __name__ == "__main__":
    _mod.serve()
