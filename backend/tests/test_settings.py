"""
app/settings.py: fail-fast configuration, environment-over-.env precedence,
secret handling, and the guarantee that tests never read your real .env.
"""

import pytest
from conftest import TEST_ENV
from pydantic import SecretStr

from app import settings as settings_module
from app.settings import Settings, SettingsError, get_settings, settings

FIELDS = set(Settings.model_fields)
ENV_NAMES = {f.upper() for f in FIELDS}


@pytest.fixture
def clean_env(monkeypatch):
    """No Settings variables in the environment."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def make_env_file(tmp_path, **values):
    path = tmp_path / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


# ── Fail fast ─────────────────────────────────────────────

def test_missing_required_values_fail_with_a_message_naming_them(clean_env, monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)   # ignore backend/.env
    with pytest.raises(SettingsError) as err:
        get_settings.__wrapped__()
    message = str(err.value)
    assert "GROQ_API_KEY is required but not set" in message
    assert "DATABASE_URL is required but not set" in message
    assert ".env.example" in message


def test_one_missing_value_is_reported_alone(clean_env, monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    with pytest.raises(SettingsError) as err:
        get_settings.__wrapped__()
    assert "DATABASE_URL" in str(err.value)
    assert "GROQ_API_KEY" not in str(err.value)


# ── Sources and precedence ────────────────────────────────

def test_real_environment_beats_env_file(clean_env, monkeypatch, tmp_path):
    env_file = make_env_file(tmp_path, GROQ_API_KEY="from-file", DATABASE_URL="sqlite://file")
    monkeypatch.setenv("GROQ_API_KEY", "from-environment")
    s = Settings(_env_file=env_file)
    assert s.groq_api_key.get_secret_value() == "from-environment"
    assert s.database_url.get_secret_value() == "sqlite://file"   # falls back to the file


def test_env_file_is_used_when_environment_is_empty(clean_env, tmp_path):
    env_file = make_env_file(tmp_path, GROQ_API_KEY="g", DATABASE_URL="d", REDIS_URL="redis://cache:6379",
                             CORS_ALLOW_ORIGINS="https://a.example,https://b.example")
    s = Settings(_env_file=env_file)
    assert s.redis_url == "redis://cache:6379"
    assert s.cors_allow_origins == "https://a.example,https://b.example"


def test_defaults_and_unrelated_keys_are_ignored(clean_env, tmp_path):
    env_file = make_env_file(tmp_path, GROQ_API_KEY="g", DATABASE_URL="d", PYTHONPATH=".", SOMETHING_ELSE="x")
    s = Settings(_env_file=env_file)
    assert s.redis_url == "redis://localhost:6379"
    assert s.chroma_path == "./chroma_db"
    assert s.cors_allow_origins is None
    assert s.cert_cache_valid_after is None
    assert s.csa_star_api_key is None


def test_env_file_is_located_relative_to_the_code_not_the_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert Settings.model_config["env_file"] == settings_module.BACKEND_DIR / ".env"
    assert settings_module.BACKEND_DIR.name == "backend"


# ── Secrets ───────────────────────────────────────────────

def test_secrets_never_appear_in_repr_or_str(clean_env):
    s = Settings(_env_file=None, groq_api_key="gsk_live_secret", database_url="postgresql://u:pw@h/db",
                 csa_star_api_key="csa-secret")
    for text in (repr(s), str(s), str(s.model_dump())):
        assert "gsk_live_secret" not in text
        assert "pw@h" not in text
        assert "csa-secret" not in text
    assert s.groq_api_key.get_secret_value() == "gsk_live_secret"


def test_groq_client_receives_the_key_from_settings(monkeypatch):
    from app.llm.config import get_llm
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_from_settings"))
    llm = get_llm(max_tokens=10)
    assert llm.groq_api_key.get_secret_value() == "gsk_from_settings"


# ── Tests never see your real .env ────────────────────────

def test_conftest_overrides_every_setting():
    # A new Settings field must be added to conftest.TEST_ENV, or its value
    # would silently come from backend/.env during tests.
    assert set(TEST_ENV) == ENV_NAMES


def test_test_process_uses_test_values_only():
    assert settings.database_url.get_secret_value() == TEST_ENV["DATABASE_URL"]
    assert settings.groq_api_key.get_secret_value() == TEST_ENV["GROQ_API_KEY"]
    assert settings.chroma_path == TEST_ENV["CHROMA_PATH"]
    assert settings.redis_url == TEST_ENV["REDIS_URL"]



# ── DATABASE_SSL and build_engine ─────────────────────────

def test_database_ssl_defaults_to_require_and_accepts_disable(clean_env, tmp_path):
    base = {"GROQ_API_KEY": "g", "DATABASE_URL": "d"}
    assert Settings(_env_file=make_env_file(tmp_path, **base)).database_ssl == "require"
    assert Settings(_env_file=make_env_file(tmp_path, **base, DATABASE_SSL="disable")).database_ssl == "disable"


def test_database_ssl_rejects_other_values(clean_env, tmp_path):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=make_env_file(tmp_path, GROQ_API_KEY="g", DATABASE_URL="d", DATABASE_SSL="yes"))


@pytest.fixture
def engine_calls(monkeypatch):
    from app.db import database
    calls = []
    monkeypatch.setattr(database, "create_async_engine", lambda url, **kw: calls.append((url, kw)) or object())
    return database, calls


PG = "postgresql+asyncpg://u:p@db.example:6543/postgres"


@pytest.mark.parametrize("setting, override, expect_ssl", [
    ("require", None, True),        # Supabase: the default
    ("disable", None, False),       # local docker-compose Postgres
    ("require", "disable", False),  # explicit override, e.g. alembic -x ssl=disable
    ("disable", "require", True),
])
def test_build_engine_ssl(engine_calls, monkeypatch, setting, override, expect_ssl):
    database, calls = engine_calls
    monkeypatch.setattr(settings, "database_ssl", setting)
    database.build_engine(PG + "?ssl=require", ssl=override)
    url, kwargs = calls[0]
    assert url == PG                                   # ?ssl=require is stripped either way
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    assert (kwargs["connect_args"].get("ssl") == "require") is expect_ssl


def test_build_engine_leaves_non_postgres_urls_alone(engine_calls):
    database, calls = engine_calls
    database.build_engine("sqlite+aiosqlite:///x.db")
    assert "connect_args" not in calls[0][1]
