# Contributing

[Русский](CONTRIBUTING.md)

Thank you for helping improve IRBIS64 Control. You can report bugs, suggest features, improve documentation, or submit code changes.

Before changing modules, read the [project architecture](../docs/ARCHITECTURE.md).

By participating in the project, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities according to the [Security Policy](SECURITY.md) without publishing technical details in a public issue.

## Reporting a bug

Before opening an issue, check whether the problem has already been reported. Include:

- the application and Windows versions;
- the operating mode: IRBIS64 server or local TXT database;
- the steps needed to reproduce the problem;
- the expected and actual results;
- the error message or a relevant log excerpt;
- a small anonymized data sample when it is required to reproduce the problem.

Do not attach real passwords, private server addresses, complete library databases, or other confidential data.

## Proposing changes

1. Fork the repository and create a separate branch from the latest `main` branch.
2. Keep each pull request focused on one complete change.
3. Preserve existing public APIs and file formats unless the change specifically requires otherwise.
4. Add tests for new behavior and bug fixes.
5. Update user documentation when application behavior changes.
6. Describe the problem, the implemented solution, and the completed checks in the pull request.

## Local checks

The project requires Python 3.11 or newer. Install the dependencies in a virtual environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install "ruff>=0.6,<1"
```

Run these checks before submitting changes:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m compileall -q irbis_control main.py db_connector.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

For interface changes, also verify the main window and the database connection tool on Windows.

## Code style

- follow `pyproject.toml` and the style of neighboring code;
- use clear names and small functions with one responsibility;
- do not leave temporary, duplicated, or commented-out old code;
- write developer comments in Russian and explain why a non-obvious solution is needed;
- avoid adding a dependency when the task can reasonably be solved with existing tools;
- do not change the architecture or public interfaces without a clear need.

## Working with library data

Test data must be fictional or anonymized. Changes that write to IRBIS64 must preserve the change preview, MFN version verification, and the ability to restore original records.

By submitting a contribution, you confirm that you have the right to provide the added code and materials to the project.
