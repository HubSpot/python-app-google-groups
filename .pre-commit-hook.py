#!/usr/bin/env zsh
"true" """"
exec .venv/bin/python3 "$0" "$@"
"""
import sys
import unittest
from subprocess import run

from coverage import Coverage, CoverageException


def run_unit_tests() -> int:
    cov = Coverage(include="app_google_groups/*")
    cov.start()
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=".", pattern="test_*.py")
    res = unittest.TextTestRunner().run(suite)
    cov.stop()
    try:
        cov.save()
        cov.report()
    except CoverageException as ce:
        print(str(ce))
        print("Don't forget to add some unit tests!")
    num_test_fails = len(res.failures) + len(res.errors)
    return num_test_fails


if __name__ == "__main__":
    git_diff = run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter", "d"],
        capture_output=True,
        encoding="utf8",
    )
    pyfiles = [fname.strip() for fname in git_diff.stdout.split("\n") if ".py" in fname]

    if not pyfiles:
        print("No Python files in commit, skipping formatting + linting")
        sys.exit(0)

    isort = run(["sh", "-c", ".venv/bin/isort -y " + " ".join(pyfiles)])
    black = run(["sh", "-c", ".venv/bin/black " + " ".join(pyfiles)])
    flake8 = run(["sh", "-c", ".venv/bin/flake8 " + " ".join(pyfiles)])
    unit_test_run = run_unit_tests()

    if isort.returncode or black.returncode:
        print("Some files were reformatted")
    if flake8.returncode:
        print("Some files failed linting checks")
    if unit_test_run:
        print("Some unit tests failed")

    returncode = isort.returncode or black.returncode or flake8.returncode or unit_test_run

    if returncode:
        print("Check changes and try again")
    else:
        # Add all the changed files to the commit
        print("Committing the changes")
        run(["git", "add"] + pyfiles)

    sys.exit(returncode)
