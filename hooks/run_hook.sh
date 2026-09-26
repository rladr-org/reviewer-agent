#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# reviewer-agent hook launcher. Claude Code started outside a login shell (systemd, cron, an IDE)
# may not have python3 on its PATH; a bare `python3` then exits 127, which Claude Code treats as
# a NON-blocking error -- every hook, including the fail-closed commit gate, silently no-ops.
# POSIX sh only: /bin/sh is dash on Debian/Ubuntu. Always invoked as `/bin/sh run_hook.sh`, so
# its executable bit does not matter.
script="$1"
shift

# Sets `subject` to the JSON string value of the payload's "command" key, escapes left in place
# (enough to search it for a word), so "commit" in the cwd or tool_input.description does not
# block an unrelated call. Falls back to the whole payload when there is no such string value,
# erring toward blocking. Builtins only.
command_value() {
  subject=$1
  rest=${1#*\"command\"}
  [ "$rest" = "$1" ] && return
  while :; do
    case $rest in
      [[:space:]:]*) rest=${rest#?} ;;
      \"*) rest=${rest#?}; break ;;
      *) return ;;
    esac
  done
  value=
  while :; do
    case $rest in *\"*) ;; *) return ;; esac  # unterminated: judge the whole payload
    seg=${rest%%\"*}
    rest=${rest#*\"}
    value=$value$seg
    # This quote ends the value unless an odd number of backslashes precede it.
    bs=0
    t=$seg
    while :; do
      case $t in *\\) t=${t%?}; bs=$((bs + 1)) ;; *) break ;; esac
    done
    [ $((bs % 2)) -eq 0 ] && break
    value=$value\"
  done
  subject=$value
}

for py in ${REVIEWER_HOOK_PYTHONS:-python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3}; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" "$script" "$@"
  fi
done
case "$script" in
  */commit_hook.py)
    # Builtins only: with no usable PATH there is no `cat` either.
    payload=
    while IFS= read -r line || [ -n "$line" ]; do
      payload="$payload$line
"
    done
    command_value "$payload"
    case "$subject" in
      *commit*)
        echo "reviewer-agent: no python3 on the hook PATH, so the commit gate cannot review this call and blocks it (fail-closed). Put python3 on the PATH Claude Code is started with." >&2
        exit 2 ;;
    esac ;;
  */session_start_hook.py)
    echo '{"systemMessage":"[reviewer-agent] no python3 on the hook PATH: the commit gate blocks every command mentioning a commit, and stop supervision is OFF. Put python3 on the PATH Claude Code is started with, then restart."}'
    ;;
esac
exit 0
