from typer.testing import CliRunner

from eoncloak.cli import app

runner = CliRunner()


def test_anonymize_rules_only_with_mapping_and_restore(settings, tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("Write to jane.doe@example.com or +44 20 7946 0958.\n", encoding="utf-8")
    mapping = tmp_path / "note.map.json"

    result = runner.invoke(app, ["anonymize", str(source), "--no-llm", "-m", str(mapping)])
    assert result.exit_code == 0, result.output
    anonymised = tmp_path / "note.anon.txt"
    assert anonymised.read_text(encoding="utf-8") == "Write to [EMAIL_1] or [PHONE_1].\n"
    assert mapping.exists()

    result = runner.invoke(app, ["restore", str(anonymised), "-m", str(mapping)])
    assert result.exit_code == 0, result.output
    restored = tmp_path / "note.restored.txt"
    assert restored.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_stdin_to_stdout(settings):
    result = runner.invoke(app, ["anonymize", "-", "--no-llm"], input="mail: a.b@example.org")
    assert result.exit_code == 0
    assert result.stdout == "mail: [EMAIL_1]"


def test_output_dir_and_format(settings, tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("a.b@example.org", encoding="utf-8")
    result = runner.invoke(
        app, ["anonymize", str(source), "--no-llm", "--to", "docx", "-o", str(tmp_path / "out")]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "note.anon.docx").exists()


def test_local_only_refuses_remote(settings, tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("x", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "anonymize",
            str(source),
            "--provider",
            "vllm",
            "--base-url",
            "http://8.8.8.8/v1",
            "--local-only",
        ],
    )
    assert result.exit_code == 1
    assert "local_only" in result.output


def test_bad_entity_type(settings, tmp_path):
    result = runner.invoke(app, ["anonymize", "-", "--no-llm", "--entities", "PERSON,FOO"])
    assert result.exit_code == 1


def test_init(settings, tmp_path):
    target = tmp_path / "eoncloak.toml"
    assert runner.invoke(app, ["init", str(target)]).exit_code == 0
    assert "[llm]" in target.read_text(encoding="utf-8")
    assert runner.invoke(app, ["init", str(target)]).exit_code == 1


def test_check_refuses_remote_before_contacting_it(settings, monkeypatch):
    from eoncloak import cli

    def boom(*args, **kwargs):
        raise AssertionError("LLM client must not be created")

    monkeypatch.setattr(cli, "LLMClient", boom)
    result = runner.invoke(
        app, ["check", "--provider", "vllm", "--base-url", "http://8.8.8.8/v1", "--local-only"]
    )
    assert result.exit_code == 1
    assert "local_only" in result.output
