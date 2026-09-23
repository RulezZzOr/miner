#!/bin/sh
set -eu
switch_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$switch_root/switch-studio" "$@"
