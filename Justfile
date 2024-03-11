@default:
  just --list --unsorted

local_install:
  python -m pip install -e .

build:
  pdm build

publish:
  pdm publish --username __token__ --password $(<credentials/pypi-token)


