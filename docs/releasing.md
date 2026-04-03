# Releasing cross-review

This project ships Python packages to **TestPyPI** and **PyPI** via GitHub Actions using trusted publishing.

## Prerequisites

Before cutting a release, make sure:

- You can push tags to the repository
- The GitHub repository has trusted publishing configured for:
  - **PyPI** environment: `pypi`
  - **TestPyPI** environment: `testpypi`
- Local development dependencies are installed:

```bash
pip install -e ".[dev]"
```

## Versioning

Update the package version in `pyproject.toml` before tagging a release.

Example:

```toml
[project]
version = "0.1.1"
```

Use semantic versioning where practical:

- `0.1.1` for patch releases
- `0.2.0` for backward-compatible features
- `1.0.0` for the first stable release

## Pre-release checklist

Run the same checks locally that CI runs:

```bash
make dev-check
make check-dist
```

This verifies:

- formatting (`black --check`)
- linting (`flake8`, `pylint`)
- typing (`mypy`)
- security scan (`bandit`)
- tests (`pytest`)
- build metadata validity (`twine check`)

## Publish to TestPyPI

Use TestPyPI first when you want to verify packaging changes without cutting a real release.

1. Push your branch to GitHub.
2. Open **Actions** → **Publish TestPyPI**.
3. Run the workflow manually.
4. After publish succeeds, verify installation from TestPyPI:

```bash
python -m venv /tmp/cross-review-test
source /tmp/cross-review-test/bin/activate
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ cross-review
cross-review --help
cr --help
```

## Publish to PyPI

PyPI publication is triggered by pushing a version tag.

1. Ensure `pyproject.toml` has the final version.
2. Commit the version bump.
3. Create and push a tag:

```bash
git tag v0.1.1
git push origin v0.1.1
```

This triggers `.github/workflows/release.yml`, which will:

1. build the package
2. run `make dev-check`
3. run `make check-dist`
4. publish to PyPI
5. create a GitHub release with built artifacts attached

## After release

Verify the published package:

```bash
python -m venv /tmp/cross-review-release
source /tmp/cross-review-release/bin/activate
pip install cross-review
cross-review --help
cr --help
```

Also confirm that:

- the version on PyPI matches the Git tag
- the GitHub Release was created
- installation works in a clean environment

## Common failure modes

### Trusted publishing misconfiguration

If the publish step fails in GitHub Actions, confirm that the PyPI/TestPyPI project is configured to trust this GitHub repository and workflow environment.

### Version already exists

PyPI does not allow overwriting an existing version. Bump `version` in `pyproject.toml`, create a new tag, and retry.

### Build passes locally but install fails

Re-test from a clean virtualenv and verify that the console entry points are installed:

```bash
cross-review --help
cr --help
```

## Related files

- `pyproject.toml`
- `Makefile`
- `.github/workflows/release.yml`
- `.github/workflows/testpypi.yml`
