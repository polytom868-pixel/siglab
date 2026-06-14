import os
import unittest


class _LiveTestCase(unittest.TestCase):
    LIVE_ENV_VAR: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        if not cls.LIVE_ENV_VAR:
            raise unittest.SkipTest("LIVE_ENV_VAR not configured")
        if not os.environ.get(cls.LIVE_ENV_VAR, ""):
            raise unittest.SkipTest(f"{cls.LIVE_ENV_VAR} not set")

    def _skip_if_disabled(self, extra_env_var: str | None = None) -> None:
        for env_var in (self.LIVE_ENV_VAR, extra_env_var):
            if not env_var:
                continue
            if os.environ.get(env_var, "").strip().lower() in {"1", "true", "yes"}:
                raise unittest.SkipTest(f"{env_var}=1")
