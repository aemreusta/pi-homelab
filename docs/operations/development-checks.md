# Development and validation

Use Python 3.12 in the repository environment so Ansible's executable and imported
package cannot come from different Homebrew/Conda installations:

```bash
make dev
make validate
.venv/bin/pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push
```

`uv` and Bats are prerequisites (`brew install uv bats-core` on macOS). ShellCheck
is included in the development lock. `make dev-upgrade` refreshes that lock to the
latest compatible versions and synchronizes the environment; review and commit the
lock, then run validation. CI synchronizes the same universal lock. Do not install
these development tools into the Pi's system Python.

Validation includes Python lint across scripts, role modules and Mac workers;
Ansible lint and syntax checks; all root Python tests; every Bats test;
duplicate/unresolved ports; and all shell
templates and tracked shell scripts. The renderer discovers templates rather than
maintaining a second service list. Quoted Go-template delimiters have a regression
test because naive Jinja stripping previously produced invalid shell.

Commit hooks run quality checks at pre-commit and the conventional message check
at commit-msg, avoiding a second full lint pass for a commit message. The pre-push
hook runs the full lightweight Python suite. Local system hooks use the explicit
repository environment; a missing environment fails with `make dev` guidance.

Pipeline shell integration requires Bash 4+ (associative arrays). On macOS, install
Homebrew Bash; the fixtures prefer `/opt/homebrew/bin/bash` over bundled Bash 3.2.
Linux CI uses its system Bash 5.
