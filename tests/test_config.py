from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from siglab.config import SiglabConfig, _read_env_file, load_settings


class ReadEnvFileTests(unittest.TestCase):
    def test_returns_empty_dict_when_file_missing(self) -> None:
        result = _read_env_file(Path("/nonexistent/path/.env"))
        self.assertEqual(result, {})

    def test_parses_simple_key_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("KEY=value\n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "value"})

    def test_parses_multiple_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("A=1\nB=2\nC=3\n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"A": "1", "B": "2", "C": "3"})

    def test_skips_comment_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("# this is a comment\nKEY=value\n# another comment\n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "value"})

    def test_skips_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("\n\nKEY=value\n\n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "value"})

    def test_skips_lines_without_equals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("KEY=value\nNO_EQUALS_HERE\nANOTHER=ok\n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "value", "ANOTHER": "ok"})

    def test_strips_double_quotes_from_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('KEY="quoted value"\n')
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "quoted value"})

    def test_strips_single_quotes_from_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("KEY='single quoted'\n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "single quoted"})

    def test_strips_whitespace_around_key_and_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("  KEY  =  value  \n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "value"})

    def test_handles_equals_signs_in_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("KEY=value=with=multiple=equals\n")
            result = _read_env_file(env_file)
            self.assertEqual(result, {"KEY": "value=with=multiple=equals"})

    def test_handles_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("")
            result = _read_env_file(env_file)
            self.assertEqual(result, {})


class SiglabConfigConstructionTests(unittest.TestCase):
    def test_constructs_with_minimal_required_args(self) -> None:
        config = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed"),
            data_lake_dir=Path("/tmp/data"),
            artifact_dir=Path("/tmp/runs"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab.db"),
            sosovalue_api_key_override=None,
        )

        self.assertEqual(config.root_dir, Path("/tmp"))
        self.assertEqual(config.sosovalue_config_path, Path("/tmp/config.json"))
        self.assertEqual(config.generated_strategy_dir, Path("/tmp/deployed"))
        self.assertEqual(config.data_lake_dir, Path("/tmp/data"))
        self.assertEqual(config.artifact_dir, Path("/tmp/runs"))
        self.assertEqual(config.live_dir, Path("/tmp/live"))
        self.assertEqual(config.ancestry_db_path, Path("/tmp/siglab.db"))
        self.assertIsNone(config.sosovalue_api_key_override)

    def test_default_field_values(self) -> None:
        config = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed"),
            data_lake_dir=Path("/tmp/data"),
            artifact_dir=Path("/tmp/runs"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab.db"),
            sosovalue_api_key_override=None,
        )

        self.assertEqual(config.sosovalue_openapi_base_url, "https://openapi.sosovalue.com/openapi/v1")
        self.assertEqual(config.sosovalue_etf_base_url, "https://api.sosovalue.xyz")
        self.assertEqual(config.sosovalue_news_base_url, "https://openapi.sosovalue.com")
        self.assertEqual(config.sosovalue_timeout_s, 30.0)
        self.assertEqual(config.sosovalue_retries, 2)
        self.assertIsNone(config.claude_api_key)
        self.assertEqual(config.claude_model, "claude-k2.5")
        self.assertEqual(config.claude_base_url, "https://api.moonshot.ai/v1")
        self.assertEqual(config.claude_max_tokens, 32768)
        self.assertEqual(config.claude_temperature, 1.0)
        self.assertEqual(config.claude_top_p, 0.95)
        self.assertEqual(config.claude_timeout_s, 300.0)
        self.assertEqual(config.population_size, 4)
        self.assertEqual(config.llm_provider, "claude")
        self.assertEqual(config.optuna_trials, 20)
        self.assertEqual(config.memory_scope, "session_local")
        self.assertFalse(config.use_historical_seeds)
        self.assertIsNone(config.claude_thinking)
        self.assertEqual(config.claude_max_tool_rounds, 25)
        self.assertIsNone(config.deepseek_api_key)
        self.assertEqual(config.deepseek_base_url, "https://api.deepseek.com")
        self.assertEqual(config.deepseek_model, "deepseek-reasoner")
        self.assertIsNone(config.openrouter_api_key)
        self.assertEqual(config.openrouter_base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(config.openrouter_model, "openai/gpt-4.1-mini")
        self.assertIsNone(config.bai_api_key)
        self.assertEqual(config.bai_base_url, "https://api.b.ai")
        self.assertEqual(config.bai_model, "deepseek-v4-flash")
        self.assertEqual(config.tracks, ("trend_signals", "yield_flows"))
        self.assertIsNone(config.tavily_api_key)
        self.assertEqual(config.tavily_base_url, "https://api.tavily.com")
        self.assertEqual(config.tavily_max_results, 5)
        self.assertEqual(config.web_explore_results_per_query, 2)

    def test_accepts_override_values(self) -> None:
        config = SiglabConfig(
            root_dir=Path("/custom"),
            sosovalue_config_path=Path("/custom/config.json"),
            generated_strategy_dir=Path("/custom/deployed"),
            data_lake_dir=Path("/custom/data"),
            artifact_dir=Path("/custom/runs"),
            live_dir=Path("/custom/live"),
            ancestry_db_path=Path("/custom/db.sqlite"),
            sosovalue_api_key_override="sk-test-key",
            sosovalue_timeout_s=60.0,
            sosovalue_retries=5,
            claude_api_key="sk-ant-custom",
            claude_model="claude-opus-4-5",
            llm_provider="deepseek",
            population_size=8,
            optuna_trials=50,
            memory_scope="persistent",
            use_historical_seeds=True,
            deepseek_api_key="sk-ds-custom",
            bai_api_key="bai-custom",
            openrouter_api_key="or-custom",
        )

        self.assertEqual(config.sosovalue_api_key_override, "sk-test-key")
        self.assertEqual(config.sosovalue_timeout_s, 60.0)
        self.assertEqual(config.sosovalue_retries, 5)
        self.assertEqual(config.claude_api_key, "sk-ant-custom")
        self.assertEqual(config.claude_model, "claude-opus-4-5")
        self.assertEqual(config.llm_provider, "deepseek")
        self.assertEqual(config.population_size, 8)
        self.assertEqual(config.optuna_trials, 50)
        self.assertEqual(config.memory_scope, "persistent")
        self.assertTrue(config.use_historical_seeds)
        self.assertEqual(config.deepseek_api_key, "sk-ds-custom")
        self.assertEqual(config.bai_api_key, "bai-custom")
        self.assertEqual(config.openrouter_api_key, "or-custom")

    def test_noneable_fields_default_to_none(self) -> None:
        config = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed"),
            data_lake_dir=Path("/tmp/data"),
            artifact_dir=Path("/tmp/runs"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab.db"),
            sosovalue_api_key_override=None,
        )

        self.assertIsNone(config.sosovalue_api_key_override)
        self.assertIsNone(config.claude_api_key)
        self.assertIsNone(config.claude_thinking)
        self.assertIsNone(config.deepseek_api_key)
        self.assertIsNone(config.openrouter_api_key)
        self.assertIsNone(config.openrouter_reasoning_model)
        self.assertIsNone(config.openrouter_fast_model)
        self.assertIsNone(config.openrouter_http_referer)
        self.assertIsNone(config.openrouter_title)
        self.assertIsNone(config.bai_api_key)
        self.assertIsNone(config.bai_max_call_credits)
        self.assertIsNone(config.tavily_api_key)


class SiglabConfigEnsureRuntimeDirectoriesTests(unittest.TestCase):
    def test_creates_all_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SiglabConfig(
                root_dir=root,
                sosovalue_config_path=root / "config.json",
                generated_strategy_dir=root / "deployed",
                data_lake_dir=root / "data",
                artifact_dir=root / "runs",
                live_dir=root / "live",
                ancestry_db_path=root / "siglab.db",
                sosovalue_api_key_override=None,
            )

            config.ensure_runtime_directories()

            self.assertTrue((root / "data").is_dir())
            self.assertTrue((root / "runs").is_dir())
            self.assertTrue((root / "live").is_dir())
            self.assertTrue((root / "deployed").is_dir())

    def test_is_idempotent_when_dirs_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "runs").mkdir()
            (root / "live").mkdir()
            (root / "deployed").mkdir()

            config = SiglabConfig(
                root_dir=root,
                sosovalue_config_path=root / "config.json",
                generated_strategy_dir=root / "deployed",
                data_lake_dir=root / "data",
                artifact_dir=root / "runs",
                live_dir=root / "live",
                ancestry_db_path=root / "siglab.db",
                sosovalue_api_key_override=None,
            )

            config.ensure_runtime_directories()

            self.assertTrue((root / "data").is_dir())
            self.assertTrue((root / "runs").is_dir())
            self.assertTrue((root / "live").is_dir())
            self.assertTrue((root / "deployed").is_dir())


class LoadSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patcher = patch("siglab.config.os.getenv", return_value=None)
        self.mock_getenv = self.env_patcher.start()
        self.read_patcher = patch("siglab.config._read_env_file", return_value={})
        self.mock_read_env = self.read_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.read_patcher.stop()

    def test_loads_default_settings_when_no_env_overrides(self) -> None:
        config = load_settings()

        self.assertIsNone(config.sosovalue_api_key_override)
        self.assertIsNone(config.claude_api_key)
        self.assertEqual(config.llm_provider, "claude")
        self.assertEqual(config.population_size, 4)
        self.assertEqual(config.optuna_trials, 20)
        self.assertEqual(config.memory_scope, "session_local")
        self.assertFalse(config.use_historical_seeds)
        self.assertIsNone(config.bai_max_call_credits)

    def test_sosovalue_api_key_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "sk-sosovalue-test" if k == "SOSOVALUE_API_KEY" else d
        )
        config = load_settings()
        self.assertEqual(config.sosovalue_api_key_override, "sk-sosovalue-test")

    def test_sosovalue_timeout_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "45.5" if k == "SOSOVALUE_TIMEOUT_S" else d
        )
        config = load_settings()
        self.assertEqual(config.sosovalue_timeout_s, 45.5)

    def test_sosovalue_retries_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "3" if k == "SOSOVALUE_RETRIES" else d
        )
        config = load_settings()
        self.assertEqual(config.sosovalue_retries, 3)

    def test_sosovalue_base_urls_from_env_vars(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: {
            "SOSOVALUE_OPENAPI_BASE_URL": "https://custom.openapi.com",
            "SOSOVALUE_ETF_BASE_URL": "https://custom.etf.com",
            "SOSOVALUE_NEWS_BASE_URL": "https://custom.news.com",
        }.get(k, d)
        config = load_settings()
        self.assertEqual(config.sosovalue_openapi_base_url, "https://custom.openapi.com")
        self.assertEqual(config.sosovalue_etf_base_url, "https://custom.etf.com")
        self.assertEqual(config.sosovalue_news_base_url, "https://custom.news.com")

    def test_detects_claude_provider_from_api_key(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "sk-ant-test" if k == "CLAUDE_API_KEY" else d
        )
        config = load_settings()
        self.assertEqual(config.llm_provider, "claude")
        self.assertEqual(config.claude_api_key, "sk-ant-test")

    def test_claude_model_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "claude-opus-4-7" if k == "CLAUDE_MODEL" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_model, "claude-opus-4-7")

    def test_claude_base_url_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "https://custom.anthropic.com" if k == "CLAUDE_BASE_URL" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_base_url, "https://custom.anthropic.com")

    def test_claude_timeout_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "600.0" if k == "CLAUDE_TIMEOUT_S" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_timeout_s, 600.0)

    def test_claude_thinking_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "enabled" if k == "CLAUDE_THINKING" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_thinking, "enabled")

    def test_claude_max_tool_rounds_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "50" if k == "CLAUDE_MAX_TOOL_ROUNDS" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_max_tool_rounds, 50)

    def test_detects_deepseek_provider(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "sk-ds-test" if k == "DEEPSEEK_API_KEY" else d
        )
        config = load_settings()
        self.assertEqual(config.llm_provider, "deepseek")
        self.assertEqual(config.deepseek_api_key, "sk-ds-test")

    def test_deepseek_model_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "deepseek-chat" if k == "DEEPSEEK_MODEL" else d
        )
        config = load_settings()
        self.assertEqual(config.deepseek_model, "deepseek-chat")

    def test_deepseek_base_url_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "https://custom.deepseek.com" if k == "DEEPSEEK_BASE_URL" else d
        )
        config = load_settings()
        self.assertEqual(config.deepseek_base_url, "https://custom.deepseek.com")

    def test_detects_bai_provider_via_bai_api_key(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "bai-key-test" if k == "BAI_API_KEY" else d
        )
        config = load_settings()
        self.assertEqual(config.llm_provider, "bai")
        self.assertEqual(config.bai_api_key, "bai-key-test")

    def test_detects_bai_provider_via_anthropic_auth_token(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "ant-auth-test" if k == "ANTHROPIC_AUTH_TOKEN" else d
        )
        config = load_settings()
        self.assertEqual(config.llm_provider, "bai")
        self.assertEqual(config.bai_api_key, "ant-auth-test")

    def test_bai_model_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "kimi-k2.5" if k == "ANTHROPIC_MODEL" else d
        )
        config = load_settings()
        self.assertEqual(config.bai_model, "kimi-k2.5")

    def test_bai_planner_model_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "deepseek-reasoner" if k == "BAI_PLANNER_MODEL" else d
        )
        config = load_settings()
        self.assertEqual(config.bai_planner_model, "deepseek-reasoner")

    def test_bai_context_tokens_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "128000" if k == "BAI_CONTEXT_TOKENS" else d
        )
        config = load_settings()
        self.assertEqual(config.bai_context_tokens, 128000)

    def test_bai_max_call_credits_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "5000.0" if k == "BAI_MAX_CALL_CREDITS" else d
        )
        config = load_settings()
        self.assertEqual(config.bai_max_call_credits, 5000.0)

    def test_bai_max_call_credits_none_when_empty_string(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "" if k == "BAI_MAX_CALL_CREDITS" else d
        )
        config = load_settings()
        self.assertIsNone(config.bai_max_call_credits)

    def test_detects_openrouter_provider_via_api_key(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "or-key-test" if k == "OPENROUTER_API_KEY" else d
        )
        config = load_settings()
        self.assertEqual(config.llm_provider, "openrouter")
        self.assertEqual(config.openrouter_api_key, "or-key-test")

    def test_detects_openrouter_provider_via_openrouter_key_alias(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "or-alt-key" if k == "OPENROUTER_KEY" else d
        )
        config = load_settings()
        self.assertEqual(config.llm_provider, "openrouter")
        self.assertEqual(config.openrouter_api_key, "or-alt-key")

    def test_openrouter_model_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "openai/gpt-4o" if k == "OPENROUTER_MODEL" else d
        )
        config = load_settings()
        self.assertEqual(config.openrouter_model, "openai/gpt-4o")

    def test_openrouter_reasoning_model_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "openai/o3" if k == "OPENROUTER_REASONING_MODEL" else d
        )
        config = load_settings()
        self.assertEqual(config.openrouter_reasoning_model, "openai/o3")

    def test_openrouter_fast_model_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "openai/gpt-4o-mini" if k == "OPENROUTER_FAST_MODEL" else d
        )
        config = load_settings()
        self.assertEqual(config.openrouter_fast_model, "openai/gpt-4o-mini")

    def test_falls_back_to_claude_when_no_provider_keys_set(self) -> None:
        self.mock_getenv.return_value = None
        config = load_settings()
        self.assertEqual(config.llm_provider, "claude")

    def test_explicit_llm_provider_env_var_overrides_key_detection(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: {
            "LLM_PROVIDER": "deepseek",
            "CLAUDE_API_KEY": "sk-ant-test",
        }.get(k, d)
        config = load_settings()
        self.assertEqual(config.llm_provider, "deepseek")

    def test_invalid_explicit_provider_falls_through_to_key_detection(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: {
            "LLM_PROVIDER": "nonexistent",
            "DEEPSEEK_API_KEY": "sk-ds-test",
        }.get(k, d)
        config = load_settings()
        self.assertEqual(config.llm_provider, "deepseek")

    def test_population_size_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "10" if k == "SIGLAB_POPULATION_SIZE" else d
        )
        config = load_settings()
        self.assertEqual(config.population_size, 10)

    def test_optuna_trials_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "50" if k == "SIGLAB_OPTUNA_TRIALS" else d
        )
        config = load_settings()
        self.assertEqual(config.optuna_trials, 50)

    def test_memory_scope_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "persistent" if k == "SIGLAB_MEMORY_SCOPE" else d
        )
        config = load_settings()
        self.assertEqual(config.memory_scope, "persistent")

    def test_use_historical_seeds_true_when_env_var_set_to_true(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "true" if k == "SIGLAB_USE_HISTORICAL_SEEDS" else d
        )
        config = load_settings()
        self.assertTrue(config.use_historical_seeds)

    def test_use_historical_seeds_true_when_env_var_1(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "1" if k == "SIGLAB_USE_HISTORICAL_SEEDS" else d
        )
        config = load_settings()
        self.assertTrue(config.use_historical_seeds)

    def test_use_historical_seeds_true_when_env_var_yes(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "yes" if k == "SIGLAB_USE_HISTORICAL_SEEDS" else d
        )
        config = load_settings()
        self.assertTrue(config.use_historical_seeds)

    def test_use_historical_seeds_false_when_env_var_false(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "false" if k == "SIGLAB_USE_HISTORICAL_SEEDS" else d
        )
        config = load_settings()
        self.assertFalse(config.use_historical_seeds)

    def test_use_historical_seeds_defaults_to_false(self) -> None:
        self.mock_getenv.return_value = None
        config = load_settings()
        self.assertFalse(config.use_historical_seeds)

    def test_tavily_settings_from_env_vars(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: {
            "TAVILY_API_KEY": "tvly-test-key",
            "TAVILY_MAX_RESULTS": "8",
            "TAVILY_BASE_URL": "https://custom.tavily.com",
        }.get(k, d)
        config = load_settings()
        self.assertEqual(config.tavily_api_key, "tvly-test-key")
        self.assertEqual(config.tavily_max_results, 8)
        self.assertEqual(config.tavily_base_url, "https://custom.tavily.com")

    def test_web_explore_results_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "5" if k == "WEB_EXPLORE_RESULTS_PER_QUERY" else d
        )
        config = load_settings()
        self.assertEqual(config.web_explore_results_per_query, 5)

    def test_reads_env_file_values_as_fallback(self) -> None:
        self.mock_read_env.return_value = {"SOSOVALUE_API_KEY": "from-env-file"}
        config = load_settings()
        self.assertEqual(config.sosovalue_api_key_override, "from-env-file")

    def test_env_var_overrides_env_file_value(self) -> None:
        self.mock_read_env.return_value = {"SOSOVALUE_API_KEY": "from-file"}
        self.mock_getenv.side_effect = lambda k, d=None: (
            "from-env" if k == "SOSOVALUE_API_KEY" else d
        )
        config = load_settings()
        self.assertEqual(config.sosovalue_api_key_override, "from-env")

    def test_provider_config_path_from_env_overrides_default(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "/tmp/test_provider.env" if k == "SIGLAB_PROVIDER_CONFIG_PATH" else d
        )
        config = load_settings()
        self.assertIsNotNone(config)

    def test_openrouter_http_referer_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "https://myapp.com" if k == "OPENROUTER_HTTP_REFERER" else d
        )
        config = load_settings()
        self.assertEqual(config.openrouter_http_referer, "https://myapp.com")

    def test_openrouter_title_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "My App" if k == "OPENROUTER_TITLE" else d
        )
        config = load_settings()
        self.assertEqual(config.openrouter_title, "My App")

    def test_claude_max_tokens_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "65536" if k == "CLAUDE_MAX_TOKENS" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_max_tokens, 65536)

    def test_claude_temperature_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "0.5" if k == "CLAUDE_TEMPERATURE" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_temperature, 0.5)

    def test_claude_top_p_from_env_var(self) -> None:
        self.mock_getenv.side_effect = lambda k, d=None: (
            "0.9" if k == "CLAUDE_TOP_P" else d
        )
        config = load_settings()
        self.assertEqual(config.claude_top_p, 0.9)


if __name__ == "__main__":
    unittest.main()
