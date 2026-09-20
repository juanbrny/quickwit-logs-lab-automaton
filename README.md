# Quickwit Logs Lab — Ansible

[![ci](https://github.com/juanbrny/quickwit-logs-lab-automaton/actions/workflows/ci.yml/badge.svg)](https://github.com/juanbrny/quickwit-logs-lab-automaton/actions/workflows/ci.yml)

One Ansible playbook that turns a single VM into a complete, working
**upstream Quickwit** log platform: k3s, an S3 object store, a PostgreSQL
metastore (CloudNativePG with WAL archiving), Quickwit itself, **Vector** as the
ingestion front door, Traefik ingress, Grafana with the Quickwit datasource
plugin, two MCP servers so an LLM can query the logs, and an end-to-end
verification that pushes a log through the whole pipeline and reads it back.

> **Lab only.** Single node, local storage, no TLS by default — a reproducible
> test and demo environment, not a production posture.

Full documentation: **[README-detailed.md](README-detailed.md)**.

## The pipeline

```
        logs ──▶ Vector ──▶ Quickwit ──▶ object store (S3)
                   │           │
        backpressure│           │ metastore
        + transform │           ▼
                    │      PostgreSQL (CloudNativePG) ──▶ WAL to S3
                    │
                    ▼
              disk buffer (PVC)

     read paths:  Grafana (Quickwit plugin) · Quickwit UI · 2 MCP servers (LLM)
```

Vector is deployed as an **aggregator, not an agent**. It does not collect
container logs; it receives them over HTTP and sits in front of the indexer as
a backpressure and transformation layer. A disk buffer absorbs bursts and
Quickwit restarts, and blocks the sender rather than dropping events when it
fills. Senders keep their own field names — Vector maps them onto the index doc
mapping in one place.

## What you can do with it

- **Stand up a complete Quickwit log platform on one VM** — every component,
  from the Kubernetes distro up to a verified log push, in a single run.
- **Choose the S3 backend** with one variable (`s3_backend`):
  - `seaweedfs` — lightweight in-cluster store (default)
  - `aistor` — MinIO AIStor operator + tenant, in-cluster
  - `external` — point at an S3 store you already have (MinIO, StorageGRID,
    Ceph RGW, real AWS S3, ...)
- **Query the logs three ways** — Grafana with the Quickwit datasource plugin,
  Quickwit's own search UI, or an LLM through either MCP server.
- **Run it against a node anywhere.** By default all Kubernetes work executes
  *on the node* over SSH — your laptop only needs **port 22**, never the API
  server. SSM (no inbound ports at all) and LAN (workstation-driven) models also
  ship.
- **Rebuild the exact same lab every time.** Every chart, image, and tool
  version is pinned in one file (`group_vars/all/versions.yml`); upgrades are
  deliberate, one line at a time.
- **Re-run safely.** Everything is idempotent, with tags for partial runs
  (`-t storage`, `-t quickwit,vector,verify`, ...).

**Tested on SLES / openSUSE Leap 16.0** (RHEL / Rocky 10 planned next cycle).
The node needs roughly **8 vCPU / 32 GB RAM / 100 GB disk** — preflight checks
this before installing anything.

## Scenarios

| You have | Use | Ports needed |
|---|---|---|
| A VM with a public IP (cloud, hosted) | default inventory — on-node over SSH | 22 in, 443 out |
| An AWS instance, SSH disabled by policy | `hosts-ssm.yml.example` — on-node over SSM | none in, 443 out |
| A VM on your LAN / VPN | `hosts-lan.yml.example` — workstation drives it | :6443 reachable |
| An existing S3 store to test against | any of the above + `s3_backend: external` | store reachable from the cluster |

## Quick start

**Workstation prerequisites:** `ansible-core` >= 2.15, `pip install
kubernetes`, `ansible-galaxy collection install -r requirements.yml`, SSH
access to the node as a sudo-capable user. (helm and kubectl are **not** needed
on your workstation — they are installed on the node.)

```bash
# 1. Inventory — set your node's address and SSH user
cp inventory/hosts.yml.example inventory/hosts.yml
$EDITOR inventory/hosts.yml

# 2. Secrets — fill in every CHANGE_ME, then encrypt
cp group_vars/all/secrets.yml.example group_vars/all/secrets.yml
$EDITOR group_vars/all/secrets.yml
ansible-vault encrypt group_vars/all/secrets.yml

# 3. Deploy everything
ansible-playbook site.yml --ask-vault-pass
```

No external accounts are needed for the default (`seaweedfs`) backend — invent
the S3 app key pair, pick a Grafana password, and generate an MCP caller token
with `openssl rand -hex 32`. The `aistor` backend additionally needs a free
single-node eval licence and a **strong** tenant root password (newer charts
reject weak defaults like `minio123`).

When the run finishes, the `verify` role has already pushed a log through
Vector into Quickwit and read it back — the lab is live.

## Where things are afterwards

With `<node>` as your node's address:

| What | URL |
|---|---|
| Quickwit search UI | `http://<node>/ui` |
| Quickwit API | `http://<node>/api/v1/...` |
| **Vector ingest** | `http://<node>/vector` · NodePort `:30900` |
| Grafana | `http://<node>/grafana` · NodePort `:30300/grafana` |
| Grafana MCP server | `http://<node>/mcp` · NodePort `:30800/mcp` |
| Quickwit MCP server | `http://<node>/qwmcp` · NodePort `:30801/mcp` |

Send a log the way the rest of the lab does:

```bash
curl -X POST http://<node>/vector \
  -H 'Content-Type: application/x-ndjson' \
  -d '{"message":"hello","level":"info","service":"demo"}'
```

Vector normalises `message`/`level`/`service` into the index's
`body`/`severity_text`/`service_name` fields on the way through.

## Everyday commands

```bash
ansible-playbook site.yml -t storage                  # redeploy just the S3 backend
ansible-playbook site.yml -t quickwit,vector,verify   # redeploy the log path + smoke test
ansible-playbook site.yml -t verify                   # re-run the end-to-end checks alone
ansible-playbook site.yml -t preflight                # size-check a VM before anything

# on the node:
sudo kubectl get pods -A                              # inspect the cluster
sudo helm list -A                                     # what's deployed, which versions
```

## Learn more

[README-detailed.md](README-detailed.md) covers the execution models and how
on-node execution works, the ingestion path through Vector, the
storage-backend contract and how to add a backend, the credential flow
(backends publish, roles consume), version pinning and the upgrade discipline,
preflight, the operational gates encoded in the automation, and CI.
