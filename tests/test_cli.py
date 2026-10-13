"""CLI command meanings, routing, and exit codes."""

import pytest

from cli import app


@pytest.fixture
def engine_calls(monkeypatch):
    calls = []

    class FakeSession:
        def __init__(self, *args, **kwargs):
            calls.append(("load",))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ask(self, query):
            calls.append(("query", query))
            return {"status": "true"}, 0.0

        def validate(self):
            calls.append(("validate",))
            return {"schema_checks": [{"status": "satisfied"}], "violations": []}

    monkeypatch.setattr(app, "Session", FakeSession)
    return calls


def test_query_batch_shares_session(engine_calls, capsys):
    queries = ["?- x:Thing.", "?- y:Thing."]
    assert app.main(["--query", *queries, "--quiet"]) == 0
    assert engine_calls == [("load",), *(('query', text) for text in queries)]
    output = capsys.readouterr()
    assert output.out.count("true") == 2
    assert "[timing] --query" in output.err


@pytest.mark.parametrize("flag", ["--validate", "--check"])
def test_validation_commands_run_validation(engine_calls, capsys, flag):
    assert app.main([flag, "--quiet"]) == 0
    assert engine_calls == [("load",), ("validate",)]
    assert "1 schema checks, 0 issues" in capsys.readouterr().out


def test_failed_validation_has_nonzero_exit_code(engine_calls, monkeypatch, capsys):
    monkeypatch.setattr(app.Session, "validate", lambda self: {
        "schema_checks": [{"status": "violated", "detail": "missing required slot"}],
    })
    assert app.main(["--validate", "--quiet"]) == 1
    assert "missing required slot" in capsys.readouterr().out


def test_stateless_queries_use_one_shot_path(engine_calls, monkeypatch):
    calls = []

    def ask(workspace, query, **kwargs):
        calls.append((query, kwargs))
        return int(query == "?- bad:Thing.")

    monkeypatch.setattr(app.stateless, "ask", ask)
    assert app.main(["--query", "?- x:Thing.", "?- bad:Thing.", "--stateless",
                     "--timeout", "7", "--max-proofs", "123"]) == 1
    assert engine_calls == []
    assert calls == [(text, {"timeout": 7, "max_proofs": 123})
                     for text in ("?- x:Thing.", "?- bad:Thing.")]


@pytest.mark.parametrize("argv", [
    ["--validate", "?- x:Thing."],
    ["--query", "?- x:Thing.", "--validate"],
    ["--query", "?- x:Thing.", "--check"],
])
def test_query_and_validation_syntax_are_distinct(argv):
    with pytest.raises(SystemExit) as exc:
        app.build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_journey_requires_day_before_loading(engine_calls, capsys):
    assert app.main(["--journey", "8509000", "--not-before", "2026-09-17T08:00:00",
                     "--transfer-seconds", "300"]) == 1
    assert not engine_calls
    assert "--journey requires --day" in capsys.readouterr().err
