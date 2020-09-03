import os

# Project name
project_name = "Google Groups App"

# The version is kept in a text file to solve import issues
# when the project is used as a Pip git dependency
# We follow Semantic Versioning 2.0
# For recommendations on when to increment each version component
# See https://semver.org/#summary
with open(os.path.join(os.path.dirname(__file__), "version.txt"), "r") as version_file:
    version = version_file.readline().strip()
