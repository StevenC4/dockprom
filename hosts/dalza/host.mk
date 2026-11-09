# dalza: Mac mini M1 (macOS). Native node_exporter under launchd, no compose:
# see the launchd branch of the Makefile for why. mozart scrapes dalza:9100
# directly (job "dalza", PHX-67).
HOST_KIND := launchd
HOST_ROLE := node (macOS)
