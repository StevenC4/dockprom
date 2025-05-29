* Set up watchtower metrics dashboard to allow selecting individual or multiple hosts as arguments / parameters in the top bar
* Should I unpin the version numbers for the docker images?
* Prometheus: override - '--storage.tsdb.retention.time=200h' so it's a month or two on mozart and shorter on pihole (based on needs)
* Explore ansible for auto registration of "satellites" in the mozart prometheus.yml
* Set up on Chopin (with watchtower) and add config files for it
* zfs-metrics - is ofelia + textfile_collector (in prometheus) the best way to gather these zfs metrics? Is there no better way to do this?
