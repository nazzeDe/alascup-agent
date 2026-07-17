from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class RuleEntry(BaseModel):
    tool_name: str
    description: str = ""


class InputSafetyRule(BaseModel):
    id: str
    patterns: list[str]
    reason: str


class RulesConfig(BaseModel):
    whitelist: list[RuleEntry] = Field(default_factory=list)
    blacklist: list[RuleEntry] = Field(default_factory=list)
    input_safety: list[InputSafetyRule] = Field(default_factory=list)


class ServerEntry(BaseModel):
    name: str
    url: str
    transport: str = "streamable-http"


class LLMConfig(BaseModel):
    api_key: str
    api_url: str
    model: str
    summary_model: str | None = None
    fallback_model: str | None = None
    max_tokens: int = 8192


class Settings(BaseSettings):
    """Unified configuration entry-point.

    Loaded in this order (later overrides earlier):
      1. from_config_dir()  — JSON files in config/
      2. env vars            — ALASCUP_* prefix
      3. direct kwarg        — Settings(database_url="...")

    This lets production use JSON files + env-var overrides while tests
    construct Settings directly with no filesystem dependency.
    """

    model_config = {"env_prefix": "ALASCUP_", "extra": "ignore"}

    # ── Paths ────────────────────────────────────────────────────────
    config_dir: Path = Path("config")

    # ── Database ─────────────────────────────────────────────────────
    database_url: str = Field(default="", validation_alias="DATABASE_URL")

    # ── LLM (env-var overrides for llm.json) ─────────────────────────
    llm_api_key: str = ""
    llm_api_url: str = ""
    llm_model: str = ""
    llm_summary_model: str | None = None
    llm_fallback_model: str | None = None
    llm_max_tokens: int = 8192

    # ── MCP servers (env-var path override) ──────────────────────────
    servers_config: str = ""  # path to servers.json

    # ── Agent limits ─────────────────────────────────────────────────
    agent_max_iterations: int = 30
    agent_token_ceiling_ratio: float = 0.95
    context_window_size: int = 128000
    tool_request_timeout_seconds: int = 300
    toolserver_shared_secret: str = Field(
        default="", validation_alias="TOOLSERVER_SHARED_SECRET"
    )

    # ── Logging ──────────────────────────────────────────────────────
    log_level: str = "INFO"

    @classmethod
    def from_config_dir(cls, config_dir: Path) -> "Settings":
        """Load base values from JSON config files, env vars still win.

        This is the production entry-point.  JSON files supply defaults;
        environment variables (ALASCUP_*) override them.
        """
        from src.config.loader import (
            load_llm_config,
            load_rules_config,
            load_servers_config,
        )

        llm = load_llm_config(config_dir / "llm.json")
        load_servers_config(config_dir / "servers.json")
        try:
            load_rules_config(config_dir / "rules.json")
        except FileNotFoundError:
            pass

        return cls(
            config_dir=config_dir,
            llm_api_key=llm.api_key,
            llm_api_url=llm.api_url,
            llm_model=llm.model,
            llm_summary_model=llm.summary_model,
            llm_fallback_model=llm.fallback_model,
            llm_max_tokens=llm.max_tokens,
            # servers_config intentionally omitted — let env var ALASCUP_SERVERS_CONFIG
            # override, or fall back to CONFIG_DIR / "servers.json" in _build_services.
        )

    @property
    def llm_config(self) -> LLMConfig:
        """Derive LLMConfig from flat settings fields."""
        return LLMConfig(
            api_key=self.llm_api_key,
            api_url=self.llm_api_url,
            model=self.llm_model,
            summary_model=self.llm_summary_model,
            fallback_model=self.llm_fallback_model,
            max_tokens=self.llm_max_tokens,
        )
