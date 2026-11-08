# Screenshot demo

A local, fictional PRTG-NATS installation for the user guide's screenshots.
It runs the real backend from this checkout against fakes for everything it
would otherwise reach over the network, so every page looks like a healthy
installation in daily use. Nothing here runs in production or in an image.

## What is real and what is faked

Real: the backend, its database, the runtime it creates with its own setup
job, every job, deployment and audit entry (created through the API as the
account that would have done it), and a NATS server (`nats:2.15.2-alpine`
in the container `prtg-nats-guide-demo-nats`) that the five probes connect
to with their own credentials.

Faked, in `fakes.py`:

- the SSH channel to probes and iperf endpoints - answers in the helper
  protocol and keeps what each "host" holds in `STATE/fake-hosts.json`;
- the PRTG core API (`https://prtg.example.test:1616`);
- the Docker socket - `prtg-nats` maps to the demo container only, the proxy
  and the overlay hub exist as states, nothing else is touched;
- the updater container - a throwaway git checkout in `STATE/checkout`
  whose `dev` branch is four commits behind its origin;
- the two network checks of the stack verification, which go to the demo
  NATS on loopback instead of `nats.example.test`.

Two things are written to the database directly: the history is moved into
the past after seeding, and one audit entry with the result "denied" exists,
because no route writes one.

## Run it

Needs Docker, `lsof`, `uv` (or Python 3.11), and `npm` for the interface.
No root.

```bash
web/frontend/guide/demo/run.sh            # backend, NATS, seed (first run ~2 min)
web/frontend/guide/demo/frontend.sh       # Vite dev server against it
```

Both take an optional state directory (default `/tmp/prtg-nats-guide-demo`,
or `PRTG_NATS_DEMO_STATE`). The first free port from each default is used
and kept in `STATE/ports.json`:

| What | Default | URL |
| --- | --- | --- |
| Backend API | 18100 | <http://127.0.0.1:18100/api/docs> |
| NATS client port | 24561 | loopback only |
| NATS monitoring | 18222 | <http://127.0.0.1:18222/varz> |
| Interface (Vite) | 15173 | <http://127.0.0.1:15173> |

`frontend.sh` sets `VITE_API_PROXY_TARGET` to the backend's address, which
`vite.config.ts` uses for its `/api` proxy instead of port 8100.

Accounts (test values for this local instance only):

| Account | Name | Role | Password |
| --- | --- | --- | --- |
| `admin` | Alex Admin | administrator | `demo-admin-2026!` |
| `olga` | Olga Operator | operator | `demo-olga-2026!` |
| `viktor` | Viktor Viewer | viewer | `demo-viktor-2026!` |

Logs are in `STATE/backend.log` and `STATE/vite.log`.

## Stop it

```bash
web/frontend/guide/demo/stop.sh           # keeps the state for the next run
web/frontend/guide/demo/stop.sh --purge   # also deletes the state directory
```

A restart without `--purge` keeps the seeded history. The rollout that is
shown as running is started again on every start; the one from the previous
run is ended by the job runner's recovery and stays in the list as cancelled.
The NATS container is recreated on each start, so its counters begin at
zero.

## The demo world

All values are from documentation ranges or `.test`; see `world.py`.

| Probe | State |
| --- | --- |
| `probe-berlin-01` | healthy, linked in PRTG, managed over the overlay |
| `probe-hamburg-01` | two outdated sensors, not linked in PRTG yet |
| `probe-leipzig-01` | unreachable since yesterday |
| `probe-bremen-01` | healthy, not marked as entered in PRTG |
| `probe-kiel-01` | healthy, a rollout to it is running |

Files: `run.sh`, `frontend.sh`, `stop.sh` drive it; `server.py` starts the
backend with the fakes; `prepare.py` lays out the state directory; `seed.py`
writes the history; `world.py` holds the names and addresses.
