#!/usr/bin/env bash
set -euo pipefail

apt-get update

apt-get install -y \
  git ca-certificates curl wget \
  build-essential make pkg-config \
  cmake meson ninja-build \
  autoconf automake libtool gettext autopoint \
  python3 python3-pip python3-pytest python3-venv \
  perl
