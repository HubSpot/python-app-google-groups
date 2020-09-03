#!/usr/bin/env zsh
"true" """"
exec ~/dev/.env/app_google_groups/bin/python3 "$0" "$@"
"""
from os import path

from setuptools import find_packages, setup

with open(path.join(path.dirname(__file__), "app_google_groups/version.txt"), "r") as version_file:
    version = version_file.readline().strip()

# The README.md will be used as the long description
with open("README.md", "r") as readme:
    long_description = readme.read()

# If the project is imported via a Pip git dependency, the prod.in will
# not be present. Use prod.txt instead.
# prod.txt exists after running "make requirements"
requirements_prod_file = "requirements/prod.txt"
if not path.exists(requirements_prod_file):
    requirements_prod_file = "requirements/prod.in"

# Parse out the actual requirements
with open(requirements_prod_file, "r") as prod_requirements:
    requirements = [
        line.strip() for line in prod_requirements if line and line[0] not in ["-", "#"]
    ]

setup(
    name="app_google_groups",
    version=version,
    install_requires=requirements,
    python_requires=">= 3.6, < 4",
    packages=find_packages(exclude=["tests", "tests.*"]),
    package_dir={"": "."},
    entry_points={
        "console_scripts": [
            # If you would like to change the name of the CLI program, change the
            # name on the left side of the equals.
            "app_google_groups=app_google_groups:main_with_args",
        ],
    },
    package_data={
        # If there are non-python files you need to include in the project, specify
        # them here. The format is like so:
        # ("app_google_groups.data", ["local_path/local_file", "local_path/local_file_2"]),
        # The result would be a module under our project called "data" that contains the 2
        # specified files
        "app_google_groups": ["version.txt"]
    },
    # Metadata for PyPI
    long_description=long_description,
    long_description_content_type="text/markdown",
    description="Slack app for managing Google Groups",
    url="https://github.com/Hubspot/python-app-google-groups",
    project_urls={"Source": "https://github.com/Hubspot/python-app-google-groups"},
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
)
