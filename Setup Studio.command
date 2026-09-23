#!/bin/sh
set -eu
switch_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! sh "$switch_root/setup-studio"; then
  printf '\nInstallation failed. Press Enter to close.'
  read -r switch_reply
  exit 1
fi
