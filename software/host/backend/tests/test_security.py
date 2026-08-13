from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pvna_host import main  # noqa: E402
from pvna_host.api import create_app  # noqa: E402
from pvna_host.domain import RunManager, RunStore  # noqa: E402


class SecurityConfigurationTests(unittest.TestCase):
    def test_service_has_no_documentation_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = RunManager(RunStore(Path(temporary)))
            app = create_app(access_token="unit-test-token", manager=manager)
            self.assertIsNone(app.docs_url)
            self.assertIsNone(app.redoc_url)
            self.assertIsNone(app.openapi_url)

    def test_uvicorn_access_log_is_disabled_to_protect_ws_query_token(self) -> None:
        source = inspect.getsource(main.main)
        self.assertIn("access_log=False", source)
        self.assertIn('args.host != "127.0.0.1"', source)


if __name__ == "__main__":
    unittest.main()
