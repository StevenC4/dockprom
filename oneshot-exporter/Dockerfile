# docker:cli gives the Docker client on Alpine with no daemon. coreutils is added for GNU
# `date -d`, which parses Docker's RFC3339Nano FinishedAt; busybox date cannot.
FROM docker:cli

RUN apk add --no-cache coreutils

COPY export.sh /export.sh
RUN chmod +x /export.sh

ENTRYPOINT ["/export.sh"]
