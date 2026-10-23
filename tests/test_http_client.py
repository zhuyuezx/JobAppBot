import builtins
import ctypes
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


class HttpClientTests(unittest.TestCase):
    def test_macos_loads_both_frameworks_before_curl(self):
        events = []
        original_import = builtins.__import__

        def track_import(name, *args, **kwargs):
            if name == "curl_cffi":
                events.append(name)
                return SimpleNamespace(requests=object())
            return original_import(name, *args, **kwargs)

        def load(path, mode):
            self.assertEqual(mode, ctypes.RTLD_GLOBAL)
            events.append(path)
            return object()

        with patch.object(sys, "platform", "darwin"), patch.object(ctypes, "CDLL", side_effect=load), \
                patch.object(builtins, "__import__", side_effect=track_import):
            runpy.run_path(str(ROOT / "jobFilter/http_client.py"))
        self.assertEqual(events, [
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
            "/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration",
            "curl_cffi"])

    def test_other_platforms_do_not_load_mac_frameworks(self):
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform), patch.object(sys, "platform", platform), \
                    patch.object(ctypes, "CDLL") as load, \
                    patch.dict(sys.modules, {"curl_cffi": SimpleNamespace(requests=object())}):
                runpy.run_path(str(ROOT / "jobFilter/http_client.py"))
                load.assert_not_called()

    def test_each_scan_entry_imports_in_a_fresh_interpreter(self):
        # No test-runner imports may accidentally preload macOS frameworks.
        for module in ("sources", "hiringcafe", "descriptions"):
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-c", f"from jobFilter import {module}; "
                     f"session = {module}.requests.Session(impersonate='chrome'); session.close()"],
                    cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
