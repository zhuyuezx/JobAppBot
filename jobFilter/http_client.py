"""Shared curl client with deterministic macOS framework initialization."""
import ctypes
import sys

# Some curl_cffi wheels (including 0.16.3) dynamically reference these symbols
# without linking the frameworks. Fresh processes after a reboot can then fail
# before making any request. Load both explicitly instead of depending on an
# unrelated import to load them first. No effect on Linux or Windows.
# https://github.com/lexiforest/curl_cffi/issues/885
_frameworks = []
if sys.platform == "darwin":
    for name in ("CoreFoundation", "SystemConfiguration"):
        _frameworks.append(ctypes.CDLL(
            f"/System/Library/Frameworks/{name}.framework/{name}",
            mode=ctypes.RTLD_GLOBAL))

from curl_cffi import requests  # noqa: E402
