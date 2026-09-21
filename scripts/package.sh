#!/usr/bin/env bash
#
# package.sh — assemble the release tarball from a checkout.
#
#   scripts/package.sh --source <dir> --out <dir> [--version X]
#
# Produces <out>/romm-tender-<V>.tar.gz, whose single top-level directory is
# romm-tender/, and <out>/romm-tender-<V>.tar.gz.sha256 beside it in sha256sum's
# own format with the bare filename in it — which is what lets the installer run
# `sha256sum -c` from the directory it downloaded both into.
#
# It BUILDS nothing. The frontend has to be built first, and the refusal below
# says so: `mise run package` is the task that does both.

set -euo pipefail

# Every path the tarball may carry, relative to the source. A release is these
# and nothing else — an entry that is not here does not ship.
SHIPPED=(backend dist bin defaults version.txt LICENSE THIRD-PARTY-NOTICES.md)

# Pruned out of the staged copy after the fact rather than filtered during it:
# the shipped list is by path and this one is by shape, so a compiled module or
# a source map anywhere below a shipped directory goes, wherever it turns up.
PRUNE_DIRS=(__pycache__ node_modules .venv)
PRUNE_FILES=('*.pyc' '*.pyo' '*.map' '*.lock' 'settings.json' 'requirements-dev.*')

# One fixed timestamp rather than the source's last commit date: this script
# takes a directory, not a repository, so a git read would make its output
# depend on something it does not require and cannot check. Every file in the
# archive carries this, so two runs over one tree produce identical bytes.
readonly EPOCH='2000-01-01 00:00Z'

# read_version's one refusal that is not "there is no version.txt".
readonly VERSION_UNREADABLE=2

main() {
    local source="" out="" version=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --source)
                source="$(value_of "$@")" || abort "$1 needs a value"
                shift 2
                ;;
            --out)
                out="$(value_of "$@")" || abort "$1 needs a value"
                shift 2
                ;;
            --version)
                version="$(value_of "$@")" || abort "$1 needs a value"
                shift 2
                ;;
            -h | --help)
                usage
                exit 0
                ;;
            *) abort "unknown argument: $1" ;;
        esac
    done

    [ -n "$source" ] || abort "--source <dir> is required"
    [ -n "$out" ] || abort "--out <dir> is required"
    [ -d "$source" ] || abort "no such source directory: $source"

    source="$(cd "$source" && pwd)"
    # In main's own shell rather than behind a helper, because these two aborts
    # have to end the RUN: a helper whose value was taken with `$(...)` would
    # print the message and exit only its subshell.
    if [ -z "$version" ]; then
        local status=0
        version="$(read_version "$source")" || status=$?
        case "$status" in
            0) ;;
            "$VERSION_UNREADABLE") abort "$source/version.txt is empty" ;;
            *) abort "no --version given and no $source/version.txt to read one from" ;;
        esac
    fi
    [ -f "$source/dist/index.js" ] || abort "$source/dist has no index.js — build the frontend first (pnpm -C frontend build)"

    mkdir -p "$out"
    out="$(cd "$out" && pwd)"

    local archive="$out/romm-tender-$version.tar.gz"
    stage_and_archive "$source" "$archive"
    checksum "$archive"

    echo "packaged romm-tender $version"
    echo "  $archive"
    echo "  $archive.sha256"
}

usage() {
    echo "Usage: scripts/package.sh --source <dir> --out <dir> [--version X]"
}

# Ends the run. **A function whose VALUE is taken with `$(...)` never calls this**
# — it answers instead, and its caller aborts. Why, and what the gate can and
# cannot see: scripts/check_shell_answer_functions.py.
abort() {
    echo "package.sh: $1" >&2
    exit 1
}

# The value after an option, or non-zero where the option has none or an empty
# one. Empty is refused rather than carried because it is not ignored where it
# lands: `--version ""` used to fall through to version.txt, so a caller that
# asked for one version was handed another without being told.
#
# `printf` rather than `echo`, which would swallow a value of `-n` as a flag of
# its own.
value_of() {
    [ $# -ge 2 ] && [ -n "$2" ] || return 1
    printf '%s\n' "$2"
}

# The version the source states, or non-zero: 1 there is no version.txt to read,
# VERSION_UNREADABLE there is one and it says nothing. Two answers rather than
# one because they are two different things for a caller to say.
read_version() {
    local file="$1/version.txt"
    [ -f "$file" ] || return 1
    local version
    version="$(tr -d '[:space:]' < "$file")"
    [ -n "$version" ] || return "$VERSION_UNREADABLE"
    echo "$version"
}

# Copy the shipped paths under one top-level directory, prune what may not ship,
# then archive that directory. Staged rather than archived in place because the
# top-level name is romm-tender/ whatever the checkout is called, and because
# pruning a copy cannot touch the source.
stage_and_archive() {
    local source="$1" archive="$2"
    local stage
    stage="$(mktemp -d)"
    # shellcheck disable=SC2064 # expand $stage now: it is what this trap exists for
    trap "rm -rf '$stage'" EXIT

    local root="$stage/romm-tender"
    mkdir -p "$root"
    local entry
    for entry in "${SHIPPED[@]}"; do
        [ -e "$source/$entry" ] || abort "$source has no $entry"
        cp -a "$source/$entry" "$root/$entry"
    done

    local name
    for name in "${PRUNE_DIRS[@]}"; do
        find "$root" -depth -type d -name "$name" -exec rm -rf {} +
    done
    for name in "${PRUNE_FILES[@]}"; do
        find "$root" -type f -name "$name" -delete
    done
    find "$root" -name '.git*' -exec rm -rf {} +

    # gzip -n so the compressed stream carries no timestamp of its own; the tar
    # flags settle everything inside it.
    tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="$EPOCH" \
        -C "$stage" -cf - romm-tender | gzip -n -9 > "$archive"
}

# sha256sum's own format, and written from the archive's directory so the line
# carries the bare filename. `sha256sum -c` resolves what it reads relative to
# the working directory, so a path here would only verify on this machine.
checksum() {
    local archive="$1"
    (cd "$(dirname "$archive")" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
}

main "$@"
