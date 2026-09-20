#!/usr/bin/env bash

set -euo pipefail

coverage erase
coverage run manage.py test "$@"
coverage report -m
coverage xml
coverage html
