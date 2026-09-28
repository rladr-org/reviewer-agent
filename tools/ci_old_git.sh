#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# tools/ci_old_git.sh -- run the root and nested Codex suites on Ubuntu 20.04's git 2.25.1 and
# Python 3.9.
# CI runs it in an ubuntu:20.04 container; locally:
#   docker run --rm -v "$PWD":/src -w /src ubuntu:20.04 sh tools/ci_old_git.sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3.9 python3.9-venv >/dev/null
git --version
case "$(git --version)" in
  "git version 2.25."*) ;;
  *) echo "expected git 2.25.x" >&2; exit 1 ;;
esac
git config --global --add safe.directory "$PWD"
git config --global user.email ci@example.com
git config --global user.name CI
# Deliberately NO init.defaultBranch: the suites must not depend on it.
python3.9 -m venv /tmp/venv
# Hooks and installer checks resolve python3 on PATH, including in child processes.
export PATH="/tmp/venv/bin:$PATH"
/tmp/venv/bin/python -m pip install -q -r requirements-dev.txt
/tmp/venv/bin/python -m pytest -p no:cacheprovider -q
cd plugins/reviewer-agent-codex
/tmp/venv/bin/python -m pytest -p no:cacheprovider -q
