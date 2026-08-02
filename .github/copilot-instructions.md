# Copilot Coding Rules

You are working in a professional Python backend project.

## Rules
- write a change log in docs/agent_log.md after every prompt and write that you did, write down the date and time with each entry
- create a .venv directory for the virtual environment
- in tmp/examples there are assets for testing and development. Do not modfiy files in this directory, but you can copy them to other places if needed
- One class per file
- File names must be snake_case
- Class names must be CamelCase
- No public symbols outside classes, except for imports
- Prefer SQLAlchemy over raw sqlite3
- Prefer FastAPI for APIs
- Use plain HTML and CSS (no frameworks) for frontend
- make a dark design for the frontend
- Use Jinja2 for templating
- Keep html as simple as possible, no JavaScript or CSS frameworks allowed
- Take care of consistent spacing in the UI. Use padding and margins to create a visually appealing layout.
- Every piece of data must be inside the database. Even constants and defaults. The db url (with params such as timeout) shall be an env var
- Use pytest for testing
- Always create unit tests for every new class
- Test criteria: 100% code coverage, no warnings, no errors, no security issues, no code smells, no vulnerabilities
- Run tests with coverage aim only when asked, never on your own initiative, but prepare the tests after every change

## Directory layout

- All code must live under the top-level `mmo_file_tools/` directory
- All tests must live under `mmo_file_tools/tests/`

