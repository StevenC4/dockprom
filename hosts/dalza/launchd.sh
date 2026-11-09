#!/bin/bash
# Manage dalza's native node_exporter LaunchDaemon. Called by make:
#   make up      -> install    (idempotent: no restart if already current and serving)
#   make down    -> uninstall
#   make status  -> status
# macOS ships bash 3.2: keep this free of bash 4 features.
set -euo pipefail

PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
LABEL=live.carroll.node_exporter
SRC="$(cd "$(dirname "$0")" && pwd)/$LABEL.plist"
DST=/Library/LaunchDaemons/$LABEL.plist
BIN=/opt/homebrew/opt/node_exporter/bin/node_exporter
LOG=/Library/Logs/node_exporter.log
BREW_LABEL=sh.brew.node_exporter
BREW_DAEMON=/Library/LaunchDaemons/$BREW_LABEL.plist
BREW_AGENT=$HOME/Library/LaunchAgents/$BREW_LABEL.plist

healthy() { curl -sf -m 3 -o /dev/null http://localhost:9100/metrics; }
loaded()  { launchctl print "system/$LABEL" >/dev/null 2>&1; }

status() {
	echo "plist:     $([ -f "$DST" ] && { cmp -s "$SRC" "$DST" && echo "installed, matches repo" || echo "installed, DIFFERS from repo (run make up)"; } || echo "not installed")"
	if loaded; then
		launchctl print "system/$LABEL" | sed -n -E 's/^[[:space:]]*(state|pid|runs|last exit code) = (.*)/launchd:   \1 = \2/p' | head -4
	else
		echo "launchd:   not loaded"
	fi
	echo "endpoint:  $(healthy && echo "serving http://localhost:9100/metrics" || echo "NOT serving :9100")"
	# Brew's own service definitions must be gone, or two daemons fight over :9100.
	[ -f "$BREW_DAEMON" ] && echo "conflict:  $BREW_DAEMON present (sudo brew services stop node_exporter)"
	[ -f "$BREW_AGENT" ] && echo "conflict:  $BREW_AGENT present (brew services stop node_exporter)"
	healthy
}

install() {
	[ -x "$BIN" ] || { echo "node_exporter missing: brew install node_exporter" >&2; exit 1; }
	plutil -lint "$SRC" >/dev/null

	if [ -f "$DST" ] && cmp -s "$SRC" "$DST" && loaded && healthy \
		&& [ ! -f "$BREW_DAEMON" ] && [ ! -f "$BREW_AGENT" ]; then
		echo "$LABEL is current and serving :9100; nothing to do (no restart)."
		return 0
	fi

	echo "Installing $LABEL. :9100 will be down for a few seconds; that is a gap in mozart's up{instance=\"dalza:9100\"}."
	# Retire brew's definitions first so exactly one daemon owns :9100.
	if [ -f "$BREW_DAEMON" ]; then sudo brew services stop node_exporter; fi
	if [ -f "$BREW_AGENT" ]; then brew services stop node_exporter; fi
	if loaded; then sudo launchctl bootout "system/$LABEL" || true; fi

	# Log file owned by the job user, whichever side of the privilege drop launchd opens it on.
	sudo touch "$LOG"
	sudo chown nobody "$LOG"
	sudo install -m 644 -o root -g wheel "$SRC" "$DST"
	sudo launchctl bootstrap system "$DST"

	for _ in 1 2 3 4 5 6 7 8 9 10; do healthy && break; sleep 1; done
	status
}

uninstall() {
	if loaded; then sudo launchctl bootout "system/$LABEL"; fi
	if [ -f "$DST" ]; then sudo rm -f "$DST"; fi
	echo "$LABEL removed. dalza:9100 is now down; mozart's scrape_target_down fires after 5m."
}

case "${1:-}" in
	install) install ;;
	uninstall) uninstall ;;
	status) status ;;
	*) echo "usage: $0 install|uninstall|status" >&2; exit 2 ;;
esac
