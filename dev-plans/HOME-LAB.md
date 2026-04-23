# Home Lab

The physical infrastructure used to integration-test ESPHome Fleet. This is a developer-only reference — not surfaced in user docs.

## Network

- **CIDR:** `192.168.224.0/22` — one flat subnet.
- The development laptop, every ESPHome device under test, and every host listed below all live on it.
- ESPHome devices are discovered via mDNS (`_esphomelib._tcp`) on this network, and OTA upload is a direct TCP push from a worker to a device. Anything that runs a worker needs IP reachability to the targets — so the flat-network assumption is load-bearing for the end-to-end test path, not incidental.

## Hosts

All hosts below are reachable over SSH with friendly aliases configured in `~/.ssh/config` on the development laptop, so `ssh hass-4`, `ssh docker-pve`, etc. work without explicit host / user flags.

| Alias | Role |
|-------|------|
| `hass-4` | Production Home Assistant install at `192.168.225.112` — Debian 13 + Supervised HA. Target of `./push-to-hass-4.sh` and every `e2e-hass-4` Playwright run. The canonical "real HA" target. |
| `pve` | Proxmox hypervisor. |
| `docker-pve` | Ubuntu + Docker host running on `pve`. Standalone-Docker (non-HAOS) server/worker test target. |
| `optiplex-5` | Second Proxmox hypervisor. |
| `docker-optiplex-5` | Ubuntu + Docker host running on `optiplex-5`. Second standalone-Docker target — lets us exercise server-on-one-host / worker-on-another topologies on the standalone path. |

## SSH

If the key isn't already loaded in the agent:

```bash
ssh-add ~/.ssh/id_ed25519
```

After that, any `ssh <alias>` / `scp` / `rsync` against the hosts above works passwordless. The end-of-turn log tail (`ssh root@hass-4.local "ha addons logs local_esphome_dist_server"`) and `./push-to-hass-4.sh` both depend on this.

## Typical use

- **`hass-4`** — every turn deploys here; the `e2e-hass-4` Playwright suite (real compile + OTA to `cyd-office-info`) runs against it. This is what the end-of-turn smoke step exercises.
- **`docker-pve`, `docker-optiplex-5`** — where the standalone Docker install path is exercised (anything touching `Dockerfile.standalone` or the non-HAOS auth flow). Two of them so we can run server-on-one / worker-on-another topologies without co-locating.
- **`pve`, `optiplex-5`** — the hypervisors themselves. Rarely touched directly by a turn; typically only used to reboot / snapshot / reprovision their Docker VMs.
