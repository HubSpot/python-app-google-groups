VENV_DEV=.venv
ACTIVATE=source ${VENV_DEV}/bin/activate
VERSION=$(shell cat app_google_groups/version.txt)

.PHONY: venv_dev
venv_dev:
	test -e ${VENV_DEV} || virtualenv -p python3 ${VENV_DEV}
	${VENV_DEV}/bin/pip3 install pip-compile-multi pip-tools

.PHONY: requirements
requirements: venv_dev
	${ACTIVATE}; ${VENV_DEV}/bin/pip-compile-multi

.PHONY: install_reqs
install_reqs: venv_dev
	${VENV_DEV}/bin/pip-sync requirements/dev.txt

.PHONY: wheel
wheel: venv_dev cleancache
	${VENV_DEV}/bin/python3 setup.py bdist_wheel

.PHONY: pex
pex: venv_dev cleancache
	${VENV_DEV}/bin/pex \
		--python-shebang '/usr/bin/env python3' \
		--python ${VENV_DEV}/bin/python3 \
		-r requirements/prod.txt \
		-o dist/app_google_groups-${VERSION}.pex \
		-m app_google_groups .

.PHONY: test
test: install_reqs
	${VENV_DEV}/bin/isort -y app_google_groups/**/*.py tests/**/*.py
	${VENV_DEV}/bin/black app_google_groups tests
	${VENV_DEV}/bin/flake8 app_google_groups tests
	${VENV_DEV}/bin/python3 -m unittest discover -p 'test_*.py'

.PHONY: cleancache
cleancache:
	rm -rf build *.egg-info
	find app_google_groups -name __pycache__ -delete
	find app_google_groups -name \*.pyc -delete

.PHONY: clean
clean: cleancache
	rm -rf ${VENV_DEV} requirements/*.txt dist

.PHONY: init
init: requirements install_reqs
	git init .
	ln -s ../../.pre-commit-hook.py .git/hooks/pre-commit
	echo "Repo initialization completed"
