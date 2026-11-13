# Contributing

Thanks for looking. This is a small tool with a narrow job, so the best contributions are
bug reports with a reproduction, and fixes that keep it safe.

## Set up

```bash
git clone https://github.com/TrueScaleTech/docker-tidy.git
cd docker-tidy
python -m venv venv
venv/bin/pip install -e ".[dev]"          # Windows: venv\Scripts\pip
```

## Before you send anything

All four must pass; CI runs the same ones on Windows and Linux:

```bash
ruff check .
ruff format --check .
mypy
pytest
```

`pytest` needs no Docker. If you have a daemon, `pytest -m integration` also runs the
tests that use it; they create and delete only `docker-tidy-test-*` resources.

## House rules

- **A bug becomes a test first.** Write the test, watch it fail, then fix it.
- **Test data is invented.** No real container, image or volume names, ids or hostnames,
  not even in a screenshot. `tests/scenario.py` holds the shared made-up host.
- **Safety is not negotiable.** Nothing may offer or remove something in use, pass
  `--force`, call `prune`, or build a command as a string. `analysis.plan_cleanup` and
  `runner.py` are where that is enforced; changes there need tests for the refusal.
- **Decisions stay out of the widgets.** Anything the screen decides (what is selected,
  what the totals say) goes in `viewmodel.py` and is tested without a window.
- **Every exception to a lint rule has a comment saying why.**
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`,
  `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`), one change per commit, with a
  message that says why.
- **The README is checked.** Every command in it must have been run. After changing the
  command line, read the README against `docker-tidy --help`.

## Adding a command

Add it to `build_parser` in `cli.py` and a `_cmd_*` function next to the others. It should
take its `Runner` as an argument, so tests can pass `FakeDocker` from `tests/fakes.py`.
Add tests for the output, the exit codes, and the case where docker is not reachable.

## Out of scope

Pruning in one go, forced removal, removing running containers, a daemon or scheduled
cleanups, and anything that talks to the network.
