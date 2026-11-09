# Hosts

The same `make` targets work on every host. What they do on a given host comes
from `hosts/<host>/host.mk`, which is versioned. The environment file holds
secrets and local overrides only; `.env.example` is its template.

The host is detected with `hostname -s`. Override it with `make HOST=<name> ...`
(handy with `-n` to preview another host's commands). An unregistered host is a
hard error that lists the known ones.

| Target | compose hosts | launchd hosts (macOS) |
|---|---|---|
| `make up` | `docker compose ... up -d` | install/refresh the LaunchDaemon; no restart if already current |
| `make down` | `docker compose ... down` | remove the LaunchDaemon |
| `make status` | `docker compose ... ps` | plist / launchd / :9100 health, and brew conflicts |
| `make pull`, `make build` | as before | no-op |
| `make config` | `docker compose ... config --quiet` (validates, prints nothing) | `plutil -lint` |
| `make host` / `make hosts` | this host's identity / all registered hosts | same |

## Registered hosts

| Host | Kind | Role | Compose files | Prometheus config |
|---|---|---|---|---|
| mozart | compose | primary: full stack, rules, Alertmanager | base + mozart | `prometheus/mozart/` |
| dns1, dns2, dns3 | compose | dns: satellite Prometheus | base + dns | `prometheus/dnsN/` |
| chopin, brahms | compose | node: exporter only, `:9100` published | node | none (scraped by mozart) |
| dalza | launchd | node (macOS): native node_exporter | none | none (scraped by mozart) |

chopin and brahms are **defined but not deployed** (2026-11-08), and mozart does
not scrape them yet. Add a job to `prometheus/mozart/prometheus.yml` like the
`dalza` one when they are brought up.

**wireguard1 is deliberately unregistered.** It was repurposed as a PiKVM on
2026-11-02, yet its `:9090` still answers as the old dockprom stack (PHX-35).
`prometheus/wireguard1/` is kept until that is settled. Running `make` there
errors out rather than guessing which compose files it used.

## Why dalza is not a compose host

On macOS, Docker runs containers inside a Linux VM. A `nodeexporter` container
would report the VM's CPU, memory, disk and uptime rather than the Mac's, and it
would go down whenever Docker Desktop/OrbStack does: it starts at login, not boot,
and restarts itself to update. For a reachability measurement like
`up{instance="dalza:9100"}` that adds gaps that are not dalza's.

So dalza runs Homebrew's `node_exporter` binary under our own LaunchDaemon,
`hosts/dalza/live.carroll.node_exporter.plist`. It starts at boot, has `KeepAlive`,
runs as `nobody`, and disables the `thermal` collector, which fails every scrape
on Apple Silicon. `make up` retires brew's own `sh.brew.node_exporter` service
definitions so only one daemon owns `:9100`. It needs sudo for the install itself.

Upgrading: `brew upgrade node_exporter`, then `make up`. If brew complains about
root-owned paths left by an earlier `sudo brew services start`, it lists them;
`sudo rm` those and retry.

## Adding a host

1. Create `hosts/<name>/host.mk` with `HOST_KIND` (`compose` or `launchd`),
   `HOST_ROLE`, and `COMPOSE_FILES` for compose hosts.
2. If the role runs a Prometheus (the base file does), add
   `prometheus/<name>/prometheus.yml`. The base file mounts it from `${HOSTNAME}`,
   which the Makefile pins to the detected host name.
3. Create the environment file from `.env.example` if the role needs secrets.
4. `make host`, `make config`, then `make up`.

## Layout note

Prometheus configs stay at `prometheus/<host>/` rather than moving under
`hosts/<host>/`. Moving them changes the bind mount, which recreates the
Prometheus container on the next `make up`. On mozart that would interrupt the
PHX-67 observation window (closes 2026-11-15). Revisit after it closes if wanted.
