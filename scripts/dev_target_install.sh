#!/bin/sh
# Installs this machine as a dev target for one build host: setup:remote-target
# sends it through the setup login, or prints it with --print, with its values
# assigned where the VALUES line stands and the dispatcher's text in its
# heredoc. What it leaves: docs/contributing/frontend-dev-loop.md, "What the
# setup does".
#
#   sh <this file> [address]
#
# Restricts the build host's key to [address], the address this machine sees
# the build host at; without one, to the address shown before anything is
# written. Running it again changes what changed.
set -eu
umask 077
# @VALUES@
printf '%s\n' "${started_marker:?}"
: "${installed_marker:?}" "${stopped_marker:?}" "${public_key:?}" "${build_host:?}" "${written:?}"
: "${target_root:?}" "${show_host_keys:?}" "${host_key_dir:?}" "${default_from?}"

from_address="${1:-$default_from}"
if [ -z "$from_address" ]; then
    client="${SSH_CLIENT:-}"
    from_address="${client%% *}"
fi
case "$from_address" in
    '' | *[!0-9A-Fa-f.:]*)
        printf '%sno address to restrict the key to (got "%s").\n' "$stopped_marker" "$from_address" >&2
        exit 1
        ;;
esac
printf 'tender-dev-setup: the key from %s will be accepted from %s only.\n' "$build_host" "$from_address" >&2

root="$HOME/$target_root"
keys="$HOME/.ssh/authorized_keys"
# A symlinked file (a dotfile manager's) is written through: the new file is
# built beside the file the link names and replaces that one, and the link stays.
if [ -L "$keys" ]; then
    keys=$(readlink -f "$keys")
fi
new_dispatcher="$root/.dispatch.sh.new"
new_keys="$keys.tender-dev.new"
# Both files are built beside their places first and moved there last, so a
# step that fails moves nothing into place and leaves no half-built file.
trap 'rm -f "$new_dispatcher" "$new_keys"' EXIT
mkdir -p "$root" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

cat > "$new_dispatcher" <<'TENDER_DEV_DISPATCHER'
@DISPATCHER@
TENDER_DEV_DISPATCHER
chmod 755 "$new_dispatcher"

key_comment="${public_key##* }"
key_blob="${public_key#* }"
key_blob="${key_blob%% *}"
line="from=\"$from_address\",command=\"$root/dispatch.sh\",restrict $public_key"
comment_line="# $key_comment: written by setup:remote-target from $build_host, $written. Running it again changes it."

# Every line of this build host's key goes — by its key, by its comment, and the
# comment line written above it — and every other line stays.
# shellcheck disable=SC2016 # $0 and $NF are awk's, not the shell's
ours='index($0, blob) || $NF == comment || ($1 == "#" && $2 == comment ":")'
lines_before=0
lines_ours=0
if [ -f "$keys" ]; then
    awk -v blob="$key_blob" -v comment="$key_comment" "!($ours)" "$keys" > "$new_keys"
    lines_before=$(awk 'END { print NR }' "$keys")
    lines_ours=$(awk -v blob="$key_blob" -v comment="$key_comment" "$ours { n++ } END { print n + 0 }" "$keys")
else
    : > "$new_keys"
fi
printf '%s\n%s\n' "$comment_line" "$line" >> "$new_keys"
chmod 600 "$new_keys"

# The new file has to be the old one less this build host's lines, plus the two
# written here: replacing authorized_keys with a short copy would take every
# other key in it along.
if [ "$(awk 'END { print NR }' "$new_keys")" -ne $((lines_before - lines_ours + 2)) ] ||
    [ "$(tail -n 1 "$new_keys")" != "$line" ]; then
    printf '%sthe new %s did not come out whole, so it was left as it was.\n' "$stopped_marker" "$keys" >&2
    exit 1
fi
replace_keys=yes
if [ -f "$keys" ] &&
    [ "$(grep -vF -- "# $key_comment: " "$keys" || true)" = "$(grep -vF -- "# $key_comment: " "$new_keys" || true)" ]; then
    printf 'tender-dev-setup: %s already holds this line; left as it was.\n' "$keys" >&2
    replace_keys=no
fi

mkdir -p "$root/current" "$root/incoming"
mv -f "$new_dispatcher" "$root/dispatch.sh"
if [ "$replace_keys" = yes ]; then
    mv -f "$new_keys" "$keys"
fi

if [ "$show_host_keys" = yes ]; then
    printf '\nThis machine'"'"'s host key fingerprints. The setup run again on %s shows one of them\n' "$build_host"
    printf 'before it connects for the first time; go on only if it is one of these:\n'
    shown=no
    for host_key in "$host_key_dir"/ssh_host_*_key.pub; do
        [ -f "$host_key" ] || continue
        ssh-keygen -lf "$host_key"
        shown=yes
    done
    [ "$shown" = yes ] || printf '  none found in %s — is an SSH server set up here?\n' "$host_key_dir"
    printf '\n'
fi
printf '%s%s\n' "$installed_marker" "$from_address"
