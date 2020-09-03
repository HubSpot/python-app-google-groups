# Google Groups App

Slack app for managing Google Groups

## Deployment

```bash
# Ensure dependencies have been generated
make requirements
# Build a Python wheel
make wheel
```

## Development

### Quick Start

```bash
# Initialize the repo (Add git hooks and create venv)
make init
# Run the project
source ~/dev/.env/app_google_groups/bin/activate
python3 -m app_google_groups
# This also works
python3 app_google_groups/main.py
# Nuke everything that was created dynamically so you can start again
make clean
```

### Running tests

The test suite requires an operational mysql server in order to function.
To configure the credentials, copy [tests/\_dbcreds.example.py](tests/_dbcreds.example.py)
to `tests/_dbcreds.py`, and set your credentails appropriately.

Once that's done you can run `make test` to run the test suite.

Note this is a CI-free project, and tests will be run as part of the commit
hook when you make a commit, and the coverage report will be appended to
the message.

### Project Structure

All Python code for the project lies under the [app_google_groups](app_google_groups) directory.
The [**main**.py](app_google_groups/__main__.py) is automatically read by
Python when the project is loaded as a module (-m flag on the interpreter).
This makes it convenient to run and allows for correct relative import resolution regardless
of other installations of the project on the system. [main.py](app_google_groups/main.py)
is the real main file for the project, and where you should put the initialisation of your code.

The [setup.py](setup.py) is also a Python module in itself, and offers a bunch of commands
for distribution management. The only one in use right now is `bdist_wheel`, which is called
by the "wheel" Make target. To see the other commands, run `python3 -m setup.py --help-commands`.

When the wheel is installed, the project can either be imported like any other Python module in
other Python code, or you can run it at the command line by calling `app_google_groups`.
This executable is created by Pip during install, and is platform agnostic. If you are writing
a pure library, you may want to remove the `entry_points` section from setup.py.

Under [requirements](requirements), there are 2 .in files and 2 .txt files after
running `make requirements`. The dev.in and prod.in is where you should specify dependencies
for either environment. The .txt files are "compiled" lists of requirements, which pin versions
for all packages required plus their depenencies. This prevents your environment drifiting
over time. These files can be committed to Github if you really want, however your constraints
in the .in files should be sufficient to keep your project working.
When you change the .in files make sure to run `make requirements` again.

The [**init**.py](app_google_groups/__init__.py) should not contain any actual code,
just imports. Constants may be able to break the exception, but creating a `constants.py` makes
importing your constants simpler everwhere. That's not to say you can't import constants.py in the
init.py, you should also do that.
