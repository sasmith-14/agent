from pathlib import Path

import pytest

from pilot.config import ConfigError, load_config


def test_yaml_and_nested_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text("provider: ollama\nollama:\n  model: demo\nlimits:\n  max_steps: 4\n")
    monkeypatch.setenv("PILOT_LIMITS__MAX_STEPS", "2")
    config = load_config(path)
    assert config.ollama.model == "demo"
    assert config.limits.max_steps == 2
    assert config.limits.max_total_tokens == 20000


def test_dotenv_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=secret\nPILOT_GEMINI__MODEL=demo\n")
    config = load_config()
    assert config.gemini_api_key.get_secret_value() == "secret"
    assert "secret" not in config.model_dump_json()


@pytest.mark.parametrize(
    "content",
    ["[]", "limits: {max_steps: 0}", "provider: unknown", "bad: field", "[broken", "1: a"],
)
def test_invalid_config(tmp_path: Path, content: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "invalid.yaml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_config(path)


def test_explicit_missing_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "absent.yaml")


def test_cli_provider_beats_env_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PILOT_PROVIDER", "gemini")
    path = tmp_path / "config.yaml"
    path.write_text("ollama: {model: local}\n")
    assert load_config(path, provider="ollama").provider == "ollama"
    with pytest.raises(ConfigError):
        load_config(path, provider="invalid")


def test_two_config_files_stay_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("provider: ollama\nollama: {model: first}\n")
    b.write_text("provider: ollama\nollama: {model: second}\n")
    first, second = load_config(a), load_config(b)
    assert first.ollama.model == "first" and second.ollama.model == "second"


@pytest.mark.parametrize("key", ["gemini_api_key", "GEMINI_API_KEY", "PILOT_GEMINI_API_KEY"])
def test_yaml_secrets_rejected(tmp_path: Path, key: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(f"{key}: never-echo-me")
    with pytest.raises(ConfigError, match="not YAML") as exc:
        load_config(path)
    assert "never-echo-me" not in str(exc.value)


@pytest.mark.parametrize(
    "endpoint", ["https://", "file:///tmp", "http://host:bad", "https://a?key=x"]
)
def test_invalid_endpoint(tmp_path: Path, endpoint: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(f"gemini: {{model: demo, base_url: '{endpoint}'}}")
    with pytest.raises(ConfigError, match="endpoints"):
        load_config(path)
