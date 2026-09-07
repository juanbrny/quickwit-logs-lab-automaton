# BYOC Logs Lab — Ansible

[![ci](https://github.com/juanbrny/byoc-logs-lab-automaton/actions/workflows/ci.yml/badge.svg)](https://github.com/juanbrny/byoc-logs-lab-automaton/actions/workflows/ci.yml)

One Ansible playbook that turns a single VM into a complete, working
**Datadog BYOC Logs** lab: k3s, an S3 object store, a PostgreSQL metastore
(CloudNativePG with WAL archiving), the CloudPrem chart, ingress, the Datadog
agent for self-monitoring, and an end-to-end log-push verification at the end.

> **Lab only.** Single node, local storage, no TLS by default — a reproducible
> test/demo environment, not a production posture.

Full documentation: **[README-detailed.md](README-detailed.md)**.

## What you can do with it

- **Stand up a complete BYOC Logs environment on one VM** — every component,
  from the Kubernetes distro up to a verified log push, in a single run.
- **Choose the S3 backend** with one variable (`s3_backend`):
  - `aistor` — MinIO AIStor operator + tenant, in-cluster (default)
  - `seaweedfs` — lightweight in-cluster alternative
  - `external` — point at an S3 store you already have (MinIO, StorageGRID,
    Ceph RGW, real AWS S3, ...)
- **Run it against a node anywhere.** By default all Kubernetes work executes
  *on the node* over SSH — your laptop only needs **port 22**, never the API
  server. SSM (no ports at all) and LAN (workstation-driven) models also ship.
- **Rebuild the exact same lab every time.** Every chart, image, and tool
  version is pinned in one file (`group_vars/all/versions.yml`); upgrades are
  deliberate, one line at a time.
- **Re-run safely.** Everything is idempotent, with tags for partial runs
  (`-t storage`, `-t byoc,verify`, ...).

**Tested on SLES / openSUSE Leap 16.0** (RHEL/Rocky 10 planned next cycle).
The node needs the resources of the BYOC runbook target: ~20 vCPU / 64 GB RAM /
100 GB disk — preflight checks this before installing anything.

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
access to the node as a sudo-capable user. (helm/kubectl are **not** needed on
your workstation — they're installed on the node.)

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

You'll need a **Datadog API key** and, for the default AIStor backend, a free
single-node **AIStor eval license** plus a **strong** tenant root password
(newer charts reject weak defaults like `minio123`).

When the run finishes, the `verify` role has already pushed a test log through
the full pipeline — the lab is live.

## Everyday commands

```bash
ansible-playbook site.yml -t storage         # redeploy just the S3 backend
ansible-playbook site.yml -t byoc,verify     # redeploy BYOC + smoke test
ansible-playbook site.yml -t preflight       # size-check a VM before anything

# on the node:
sudo kubectl get pods -A                     # inspect the cluster
sudo helm list -A                            # what's deployed, which versions
```

## Learn more

[README-detailed.md](README-detailed.md) covers the execution models and how
on-node execution works, the storage-backend contract and how to add a backend,
the credential flow (backends publish, roles consume), version pinning and the
upgrade discipline, preflight, the hard-won operational gates encoded in the
automation, and CI.
