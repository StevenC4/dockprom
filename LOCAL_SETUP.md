# Local Setup

This document describes some environment-specific setup instructions.

## Running exporters only

If you have the full project running on another host and you want to gather metrics from an additional host, you can run the "exporters-only" version of this project.

To do so, add the following to your `.env` file:

```
EXPORTERS_ONLY=true
```

To start and stop the containers, use `make up` and `make down` rather than `docker compose up -d` and `docker compose down`, as the `Makefile` will switch between `docker-compose.yml` and `docker-compose.exporters.yml` based on the presence of `EXPORTERS_ONLY=true` in your `.env` file.

