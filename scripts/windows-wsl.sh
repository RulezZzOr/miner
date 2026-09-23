#!/bin/bash
# Invoked through stdin by cmd.exe; paths are arguments, never shell source.
set -eu
if ! grep -qi 'microsoft.*wsl2\|microsoft-standard' /proc/sys/kernel/osrelease; then
  echo 'Switch Studio vyžaduje WSL2. Ověřte: wsl --list --verbose' >&2
  exit 1
fi
switch_root=$(wslpath -a -u "$1")
shift
echo 'Switch Studio běží uvnitř WSL2. Adresa pro prohlížeč se zobrazí níže.'
echo 'Cesty projektů mají linuxový formát, například /mnt/c/Users/you/projects.'
exec sh "$switch_root/switch-studio" "$@"
