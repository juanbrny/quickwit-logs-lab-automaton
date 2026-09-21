# Quickwit Logs Lab — detailed reference

> This is the long-form documentation. For a quick overview and the fastest
> path to a running lab, start with the main [README](README.md).

Automates a complete single-node **upstream Quickwit** log platform: k3s, an S3
object store (in-cluster or external), a PostgreSQL metastore via CloudNativePG
with WAL archiving, Quickwit, Vector as the ingestion front door, Traefik
ingress, Grafana with the Quickwit datasource plugin, two MCP servers for LLM
access, and an end-to-end log-push verification.

> **Lab only.** Single node, local storage, no TLS by default. This is a
> reproducible test and demo environment, not a production posture.

## What gets deployed

| Layer | Component | Role |
|---|---|---|
| Kubernetes | k3s (Traefik, local-path) | `k3s` |
| Object storage | SeaweedFS (default), AIStor, or external | `storage_<backend>` |
| Metastore | PostgreSQL via CloudNativePG, WAL archived to S3 | `cnpg` |
| Search engine | Quickwit (indexer, searcher, metastore, control plane, janitor) | `quickwit` |
| Ingestion | Vector — backpressure + transformation | `vector` |
| Query UI | Grafana + Quickwit datasource plugin | `grafana` |
| LLM access | Grafana MCP server, native Quickwit MCP server | `grafana_mcp`, `quickwit_mcp` |
| Checks | end-to-end verification | `verify` |

Quickwit is installed from the **upstream OSS chart**. The chart has not been
bumped for 0.9 — chart `0.8.16` still declares `appVersion v0.8.2` — so the
engine version is pinned separately via `quickwit_image_tag`, and `verify`
asserts at runtime that the cluster really answers as 0.9.x. See
[Version pinning](#version-pinning-stable-base).

## Tested platforms

- **SLES / openSUSE Leap 16.0** — the OS this is currently developed and tested
  against.
- **RHEL / Rocky 10** — planned for the next cycle; not yet validated.

Package installs use `ansible.builtin.package`, so other distros may work, but
only the above are exercised. Minimal images may lack base utilities the
playbook needs on the node (e.g. `tar`); `roles/k8s_tooling` installs the known
set.

## Execution model — where the Kubernetes work runs

By **default the Kubernetes work runs on the node**, reached over SSH. Your
workstation needs only **port 22** — nothing inbound to the API server. This is
the right model for any node behind a public IP, a NAT, or a corporate security
group, and it removes a whole class of "can't reach :6443" and
workstation-Python problems.

It works because the inventory's `k8s_control` group points at the **same host
as `lab_node`**. That makes `k8s_control_on_node` true, so the second play
executes on the instance against `127.0.0.1:6443`, and `roles/k8s_tooling`
installs helm + the python kubernetes client + kubectl there. `become` is used
throughout (assumes a non-root sudo user such as `ec2-user`; a harmless no-op if
you are already root).

| Model | inventory template | needs | when to use |
|---|---|---|---|
| **On-node over SSH** (default) | `inventory/hosts.yml.example` | port 22 | almost always — public IP, NAT, SG, or a plain remote lab |
| On-node over SSM | `inventory/hosts-ssm.yml.example` | SSM agent + S3 transfer bucket | AWS instances with SSH disabled by policy |
| Workstation drives (LAN) | `inventory/hosts-lan.yml.example` | inbound :6443 reachable | only on a LAN/VPN where you can reach the API server |

The first two are the *same* execution path (run on the node); they differ only
in transport. The third is the legacy model, kept for LAN labs where driving the
cluster from your laptop is convenient.

> **SSH keepalives matter here.** Several roles run `helm --wait` with timeouts
> of up to 600s, during which the SSH channel is completely silent. Anything
> with an idle timeout in the path (NAT, VPN, `sshd`) then closes it and Ansible
> reports `UNREACHABLE` even though the work on the node succeeded.
> `ansible.cfg` sets `ServerAliveInterval` and a matching `ControlPersist` to
> prevent that.

## Prerequisites

**Control node (your workstation):**

- `ansible-core` >= 2.15
- `pip install kubernetes` and, for the SSM model, `boto3`
- `ansible-galaxy collection install -r requirements.yml`
- SSH access to the node as a sudo-capable user
- For the SSM model only: AWS CLI v2 + `session-manager-plugin`

You do **not** need helm or kubectl on your workstation in the default on-node
model — `roles/k8s_tooling` installs them on the node.

**The node:**

- Reachable over SSH (default) or SSM
- A sudo-capable login (root not required)
- Outbound HTTPS (443) — the node itself pulls helm charts, container images,
  and the k3s installer
- A clock within a few minutes of real time. A stale clock makes TLS fail with
  `certificate is not yet valid` when the node fetches the k3s installer.
- Meets the preflight spec (see below)

## First run

1. **Inventory.** Copy the default (on-node) template and set your node:

   ```bash
   cp inventory/hosts.yml.example inventory/hosts.yml
   $EDITOR inventory/hosts.yml        # ansible_host, ansible_user, hostname
   ```

   `lab_node` and `k8s_control` point at the same host via a YAML anchor, so
   they can't drift. The node's address lives **only** here — `node_ip` is
   derived from it. Both groups must contain exactly one host; `site.yml`
   asserts this. The real `hosts.yml` is gitignored.

   For the LAN model instead, start from `inventory/hosts-lan.yml.example`; for
   SSM, `inventory/hosts-ssm.yml.example`.

2. **Secrets.** Create from the template, fill it in, encrypt:

   ```bash
   cp group_vars/all/secrets.yml.example group_vars/all/secrets.yml
   $EDITOR group_vars/all/secrets.yml       # replace every CHANGE_ME
   ansible-vault encrypt group_vars/all/secrets.yml
   ```

   Only the `.example` is committed; the real `secrets.yml` is gitignored and
   never read at runtime by its extension. Edit later without decrypting to
   disk: `ansible-vault edit group_vars/all/secrets.yml`.

   | Variable | Needed when | What it is |
   |---|---|---|
   | `grafana_admin_password` | always | Grafana admin login. `verify` also uses it to probe the provisioned datasource. |
   | `grafana_mcp_caller_token` | always | Bearer token MCP clients must present to the Grafana MCP server. Any long random string — `openssl rand -hex 32`. |
   | `s3_app_access_key` | `seaweedfs`, `aistor` | The app key you want the backend to **create** (not looked up — invent it, ~20 chars). CNPG (WAL) and Quickwit (indexes) use it. |
   | `s3_app_secret_key` | `seaweedfs`, `aistor` | Its ~40-char secret half. |
   | `seaweedfs_admin_secret_key` | `s3_backend: seaweedfs` | Admin identity secret, used only to create buckets. |
   | `aistor_license` | `s3_backend: aistor` | Free single-node eval licence from AIStor (min.io). |
   | `aistor_root_password` | `s3_backend: aistor` | Tenant root password, used only to provision buckets + the app key. **Must be strong** — newer charts reject the weak `minio/minio123` pair. |
   | `external_s3_access_key`, `external_s3_secret_key` | `s3_backend: external` | Credentials issued **by** the external store; validated and forwarded, not created. |
   | `quickwit_mcp_basic_auth_password` | only if `quickwit_mcp_basic_auth_enabled` | Password for the Traefik BasicAuth middleware in front of the native MCP server. Unused while that flag is false. |

   `site.yml` stops if the file is missing and refuses to run while any
   `CHANGE_ME` remains for the active backend.

3. **Run:**

   ```bash
   ansible-playbook site.yml --ask-vault-pass
   ```

## The ingestion path — Vector

`roles/vector` deploys Vector in the chart's **Aggregator** role, which renders
a StatefulSet with a PersistentVolumeClaim. That volume is not incidental: it
backs the sink's disk buffer.

Vector is **not** an agent here. It does not read container logs. It exposes an
HTTP source and everything pushes to it:

```
logs ──POST NDJSON──▶ Vector ──▶ Quickwit indexer (in-cluster Service)
```

Two reasons it is in the path:

- **Backpressure.** The sink has a disk buffer with `when_full: block`. When
  Quickwit is slow, restarting, or catching up, events spill to disk instead of
  being dropped; when the buffer fills, Vector pushes back on the *sender*
  rather than discarding data. The alternative (`drop_newest`) keeps producers
  fast and loses logs, which is the wrong default in front of an indexer, so it
  is not used.
- **Transformation.** A `remap` (VRL) transform normalises incoming field names
  onto the index doc mapping — `message`/`msg` → `body`, `level`/`severity` →
  `severity_text` (upper-cased), `service` → `service_name`,
  `hostname` → `host`. Senders keep their own schema; the mapping lives in one
  place.

Both are tuned in `roles/vector/defaults/main.yml`
(`vector_buffer_when_full`, `vector_buffer_max_bytes`, `vector_storage_size`,
the batch settings).

**Reaching it.** Vector's `http_server` source is configured with
`strict_path: false`, so it accepts any request path. That is what lets the
Traefik route at `/vector` and the bare NodePort both land on the same source
with no rewrite middleware — the default (`strict_path: true`, `path: "/"`)
would 404 the ingress route.

**Timestamps.** The HTTP source stamps arrival time into `timestamp`, which is
correct for live traffic. A sender that knows its own event time overrides it by
sending `event_timestamp` in RFC3339.

**Healthcheck is disabled on the sink** on purpose: Vector's HTTP healthcheck
issues a `GET` against the sink URI, and Quickwit's ingest endpoint only answers
`POST`, so it would fail forever and fill the log with meaningless errors. The
`verify` role proves the path works end to end instead.

**Observability.** Vector's `internal_metrics` are exported on `:9090` and its
API on `:8686`. Buffer depth is the metric that shows backpressure actually
happening.

> Backdated bulk loads (replaying months of history) should go **directly** to
> Quickwit's ingest endpoint, not through Vector. Vector is a live pipeline;
> pushing history through it buys nothing and re-stamps timestamps unless every
> record carries `event_timestamp`.

## Re-runs / partial runs

Everything is idempotent — `helm upgrade --install` semantics, `k8s`-module
applies, and bucket provisioning is gated behind a live bucket-access probe
(the `mc` provisioning pod runs only when the probe fails). Tags:

```bash
ansible-playbook site.yml -t distro                   # k3s only
ansible-playbook site.yml -t tooling                  # helm/kubectl/python on the node
ansible-playbook site.yml -t storage                  # the S3 backend only
ansible-playbook site.yml -t database                 # CNPG only
ansible-playbook site.yml -t quickwit                 # Quickwit + ingress
ansible-playbook site.yml -t vector                   # the ingestion front door
ansible-playbook site.yml -t grafana                  # Grafana + datasource plugin
ansible-playbook site.yml -t mcp                      # both MCP servers
ansible-playbook site.yml -t verify                   # end-to-end checks alone
```

### Usage hints

- **After a mid-run failure**, just re-run the whole playbook — idempotency
  means completed stages are no-ops. Use a tag only when you know the failure
  was contained to one stage.
- **`-t tooling` is easy to forget.** `k8s_tooling` is behind its own tag, so a
  partial run like `-t storage` does *not* install or upgrade helm. If a chart
  fails to parse, check the helm version on the node first.
- **Working on the cluster by hand:** SSH to the node and use its tooling —
  `sudo kubectl get pods -A`, `sudo helm list -A`. The kubeconfig is
  `/etc/rancher/k3s/k3s.yaml` (root-readable), which is why `sudo` is needed.
  Note that `helm` needs `KUBECONFIG` set explicitly; `kubectl` (the k3s
  wrapper) does not.
- **Switching storage backends on a live lab** is a redeploy of the stack, not
  an in-place migration: flip `s3_backend`, then re-run
  `-t storage,database,quickwit,vector,verify`. Indexes and WAL from the old
  backend are not migrated.
- **Throwaway `mc` pods** (`mc-verify`, `mc-mkbucket`, `mc-accesskey`,
  `mc-ls-wal`) are `--rm`, which only deletes them while the client stays
  attached. An interrupted run leaves them behind, and because the names are
  fixed every later attempt then fails instantly with `AlreadyExists`.
  `roles/mc_helpers/tasks/reap.yml` removes them before each `mc` step, so this
  self-heals — no manual cleanup needed.

## Version pinning (stable base)

Every external version — k3s, helm, all Helm charts, the Quickwit image tag,
the Grafana plugin, and the `mc` image — is pinned in **one file**,
`group_vars/all/versions.yml`. Nothing else uses `:latest` or an unpinned
chart, and preflight **fails** if it finds a floating tag or an unfilled
placeholder.

| Component | Chart | Version |
|---|---|---|
| k3s | — | `v1.35.6+k3s1` |
| helm (installed on the node) | — | `v3.21.4` |
| seaweedfs | `seaweedfs/seaweedfs` | `4.39.0` |
| aistor-operator | `minio/aistor-operator` | `5.9.0` |
| aistor-objectstore | `minio/aistor-objectstore` | `1.0.16` |
| cnpg | `cnpg/cloudnative-pg` | `0.29.0` |
| quickwit | `quickwit/quickwit` | `0.8.16` (engine pinned separately to `v0.9.0`) |
| vector | `vector/vector` | `0.58.0` |
| grafana | `grafana-community/grafana` | `13.2.2` |
| grafana-mcp | `grafana-community/grafana-mcp` | `0.22.0` |
| quickwit-mcp | `oci://ghcr.io/agarwalvivek29/charts/quickwit-mcp` | `0.1.1` |

Two pins deserve explanation:

- **`quickwit_image_tag: v0.9.0`.** The chart's own `appVersion` is `v0.8.2`, so
  leaving `image.tag` unset silently deploys 0.8. Every workload in the chart
  resolves its image through `.Values.image.tag | default .Chart.AppVersion`,
  so setting the tag covers all of them. `verify` then calls
  `/api/v1/version` on the running cluster and fails the run if the reported
  version is not 0.9.x — a comment would not have caught a regression here.
- **`helm_version: v3.21.4`.** The floor is 3.17.0: the SeaweedFS chart uses the
  `fromToml` template function, which does not exist in 3.16.x and fails at
  template-parse time. `k8s_tooling` compares the installed version against this
  pin rather than merely checking that some helm exists, and verifies the result
  after installing.

**Upgrading a component — one at a time:** bump exactly one line in
`versions.yml`, run the relevant tag, verify. If it breaks, revert that one line
— you know precisely what changed.

For a truly immutable base, pin `mc_image` by digest (`mc@sha256:...`) rather
than tag; chart versions are immutable once published, so a number suffices
there.

## Swapping variants

Two selectors in `group_vars/all/main.yml`:

| Variable | Maps to |
|---|---|
| `k8s_distro` | `roles/<distro>` — must install k8s, wait Ready, (for the LAN model) export the kubeconfig, and satisfy the distro contract (`ingress_flavor`, `default_storage_class`) |
| `s3_backend` | `roles/storage_<backend>` + `vars/storage_<backend>.yml` — must provision buckets, provision or accept an app key scoped to `s3_buckets`, and publish the contract |

**Ingress flavor:** every externally reachable role (`quickwit`, `vector`,
`grafana`, both MCP servers) includes `tasks/ingress_<flavor>.yml`. To support
an nginx-based distro, add `ingress_nginx.yml` to each and set
`ingress_flavor: nginx`.

## Preflight

`roles/preflight` runs on the node, from gathered facts, **before k3s is
installed** — an undersized VM fails in seconds instead of after a long run
ending in Pending pods.

```yaml
preflight_required_vcpu: 8
preflight_required_ram_gb: 32
preflight_required_disk_gb: 50
```

Raw OS values (`ansible_processor_vcpus`, `ansible_memtotal_mb`, `df`). RAM gets
5% slack because `/proc/meminfo` reports a little under nominal (firmware and
kernel reserve) — a 32 GB VM shows ~31 GB; the check catches the wrong VM size,
not bytes. Preflight also asserts every version in `versions.yml` is pinned,
including `quickwit_image_tag`.

Override in `group_vars/all/main.yml`; bypass with `-e preflight_skip=true`. Run
alone with `-t preflight`.

## Credential flow

Storage backends **publish** the S3 app credentials; downstream roles
**consume** them. The direction matters because backends differ in who picks the
keys:

| Backend | Who chooses the credentials |
|---|---|
| `seaweedfs`, `aistor` | **You do.** `s3_app_access_key` / `s3_app_secret_key` from the vault are an *input*; the backend creates that key and scopes it to `s3_buckets`. |
| `external` | **The other system does.** `external_s3_access_key` / `_secret_key` are issued elsewhere; the playbook validates and forwards them. |
| a future `rook` | **Ceph does.** It generates them and returns a Secret; the role would read and publish them. |

Every storage role ends by calling `s3_credentials/publish`, which sets facts
for this run **and** writes Secret `lab-s3-credentials` into the storage
namespace. Downstream (`cnpg`, `quickwit`, `verify`) never reads `s3_app_*` or
`external_s3_*` — only the published `s3_access_key` / `s3_secret_key`.

That Secret is why partial runs work: `-t quickwit` on an existing deployment
resolves the credentials from it (`s3_credentials/main`) without re-running the
storage role. If neither the facts nor the Secret exist, you get a clear failure
instead of an undefined-variable trace.

### Variable scope — a rule worth knowing

Role defaults exist **only while that role runs**. Any variable read by more
than one role — anything `verify` touches, for instance — must live in
`group_vars/all/main.yml`, not in a role's `defaults/`. Running the full
playbook can mask a violation because an earlier role's variables may linger in
scope; `-t verify` alone exposes it immediately. This bit the repo three times
before the rule was written down.

## Storage backends

`s3_backend` selects both the role and the contract file. Shipped: `seaweedfs`
(default), `aistor`, `external`. The contract (`vars/storage_<backend>.yml`) is
what downstream roles consume — they never reference a backend by name:

| key | seaweedfs | aistor | external |
|---|---|---|---|
| `s3_namespace` | `seaweedfs` | `primary-object-store` | `lab-s3` (mc pods + creds Secret only) |
| `s3_scheme` | http | http | usually **https** |
| `s3_host` | in-cluster service FQDN | in-cluster service FQDN | whatever you point it at |
| `s3_port` | **8333** | **80** | 443 |
| `s3_nodeport` | 31101 | 31001 | — |
| `s3_force_path_style` | true | true | true (false for real AWS S3) |

`s3_endpoint` is always derived as `{scheme}://{host}:{port}` — CI fails any
contract that hardcodes it. `s3_service_name` is **backend-internal** (seaweedfs
and aistor use it to build the FQDN and assert the Service exists); external
has no k8s Service, so it isn't in the contract.

> **AIStor port note:** the pinned `aistor-objectstore` chart (`1.0.16`) exposes
> the S3 API on **port 80** (NodePort 31001), not 9000. The contract reflects
> this. Older charts used 9000 — another reason the version is pinned.

### Inputs vs contract

Each backend takes its own `<backend>_s3_*` **inputs** from
`group_vars/all/main.yml` and maps them onto the neutral `s3_*` **contract**
keys, so the knob you reach for doesn't depend on which backend is active:

```yaml
seaweedfs_s3_scheme: http   # -> s3_scheme
aistor_s3_scheme: http      # -> s3_scheme
external_s3_scheme: https   # -> s3_scheme
```

The scheme is not cosmetic — it drives the deployment. `aistor_s3_scheme: https`
sets `disableAutoCert: false` so the operator issues a self-signed cert; pair it
with `s3_tls_skip_verify: true` so `mc` (which every probe runs) gets
`--insecure`. SeaweedFS **asserts** `http` (its TLS wiring isn't implemented
here). CI fails any contract that hardcodes a scheme.

### How seaweedfs and aistor differ

Both publish the same contract and use the same probe-first idempotency (verify
with the app key → provision only on failure → re-probe as the verdict) and the
same shared `templates/mc_verify.sh.j2`. They differ where it belongs — inside
the role:

- **Bucket + key provisioning.** AIStor provisions buckets and the scoped app
  key *after* deploy via throwaway `mc` pods (`mc mb`, then
  `mc admin accesskey create` with an IAM-style JSON policy). SeaweedFS has no
  `mc admin` equivalent: identities are declared in a JSON config Secret the
  gateway reads *at startup*, with per-bucket actions.
- **Readiness gate.** AIStor polls its ObjectStore CR for `healthStatus: green`,
  then asserts the S3 Service exists. SeaweedFS gates on the S3 Service having
  ready endpoints. The `mc` provisioning script itself uses `mc ready` (not
  `mc ls`, which the AIStor root user is denied on newer charts).
- **Service naming.** AIStor's service name is chosen by the operator and has
  drifted between versions; the role assumes `minio` (contract) and tracks the
  CR name separately. SeaweedFS pins `fullnameOverride` for determinism.

### `s3_backend: external` — an unmanaged store

Points the stack at an S3 store this playbook does not own: an existing MinIO,
StorageGRID, ECS, a Rook/Ceph RGW on another cluster, or real AWS S3. Nothing is
provisioned. The role asserts the store is fully specified, optionally creates
buckets (`external_s3_create_buckets`, off by default — most managed stores hand
you a key that can't CreateBucket), runs the **same** `mc_verify` probe every
backend must pass, and publishes the contract.

```yaml
# group_vars/all/main.yml
s3_backend: external
external_s3_scheme: https
external_s3_host: s3.internal.example.com
external_s3_port: 443
external_s3_force_path_style: true    # false for real AWS S3
```

```yaml
# vaulted secrets.yml
external_s3_access_key: "..."
external_s3_secret_key: "..."
```

Buckets in `s3_buckets` must already exist (or set `external_s3_create_buckets`).

**Rook/Ceph** is deliberately *not* an in-repo backend. Single-node Rook needs a
raw block device the lab VM doesn't have, plus non-default tunables
(`mon.count: 1`, `failureDomain: osd`, replica size 1 with
`requireSafeReplicaSize: false`), and it competes with the Quickwit pods for
RAM. Provision Ceph separately and consume its RGW endpoint through `external` —
same contract, none of the coupling.

### Adding a backend

1. `roles/storage_<name>/` — deploy it, gate on ready, provision buckets and a
   key scoped to `s3_buckets`, then call `s3_credentials/publish` with whatever
   credentials it ended up with.
2. `vars/storage_<name>.yml` — publish the contract (`s3_namespace`, `s3_scheme`,
   `s3_host`, `s3_port`, `s3_endpoint`, `s3_endpoint_external`,
   `s3_force_path_style`). CI fails if a key is missing.
3. Set `s3_backend: <name>`. Nothing else changes.

## The index

The Quickwit chart's bootstrap job seeds one index, `lab-logs`
(`quickwit_index_id`), created idempotently (`index describe || index create`).
Its doc mapping is `mode: dynamic` with five typed fields:

| Field | Type | Why it is typed |
|---|---|---|
| `timestamp` | datetime, fast | The Grafana plugin **refuses the datasource** if the timestamp field has no explicit `output_format`. |
| `body` | text, `record: position` | Positions enable phrase search — needed to hunt a literal string such as an IP address. |
| `severity_text` | text, raw, fast | The plugin requires a fast log-level field. |
| `service_name` | text, raw, fast | Grouping and filtering. |
| `host` | text, raw | Filtering. |

Vector's `normalize` transform maps incoming logs onto exactly these names. If
you change one, change it in both places — under `mode: dynamic` a mismatch is
silent: the field is stored untyped and searches quietly miss.

## MCP servers — LLM access to the logs

Two are deployed, side by side, because they are good at different things.

| | `grafana_mcp` | `quickwit_mcp` |
|---|---|---|
| Path | `/mcp`, NodePort 30800 | `/qwmcp`, NodePort 30801 |
| Talks to | Grafana (service account) | Quickwit directly |
| Tools | `query_quickwit` and the enabled Grafana categories | `list_indexes`, `describe_index`, `search_logs`, `aggregate_logs`, `get_query_syntax_help` |
| Aggregations | **no** — documents only, max 100 per call | **yes** — `aggregate_logs` returns counts |
| Auth | bearer token (`grafana_mcp_caller_token`) | **none of its own** — optional Traefik BasicAuth |

The aggregation gap is the practical difference. Asking "when did this start?"
over twelve months is one `date_histogram` call on the native server, versus
several bisecting document searches through Grafana's.

The Grafana MCP server's service account token is minted through the Grafana API
**only when the Kubernetes Secret is absent** — such a token is readable exactly
once, at creation, so an unconditional run would orphan a token on every
re-run.

> **Read before exposing `quickwit_mcp`.** v0.0.4 has no authentication at all.
> It is read-only, but anyone who can reach the port can read every log.
> `quickwit_mcp_basic_auth_enabled: true` puts Traefik BasicAuth in front, which
> is the only authentication available today. It defaults to **off** because
> turning it on breaks clients that cannot send basic-auth credentials.

The Grafana MCP server also needs `--allowed-hosts` set: its host-header
validation defaults to loopback variants of the listen address, so every request
arriving via NodePort or Traefik would otherwise be rejected with 403 —
including health probes.

## Layout

```
site.yml                      play 1: node prep + k8s distro (on the node)
                              play 2: the stack (on the node by default)
ansible.cfg                   SSH keepalives for long helm --wait steps
group_vars/all/
  main.yml                    selectors, derived vars, per-backend inputs
  versions.yml                pinned chart/image/tool versions (single source)
  secrets.yml.example         vault template
inventory/
  hosts.yml.example           on-node over SSH (default)
  hosts-ssm.yml.example       on-node over SSM
  hosts-lan.yml.example       workstation drives the cluster (LAN)
vars/storage_<backend>.yml    the storage contract, per backend
templates/                    shared mc scripts (verify, make-buckets)
roles/
  preflight/                  VM size + version-pinning asserts
  k8s_tooling/                helm + python k8s client + kubectl on the node
  k3s/                        distro role: install, wait Ready, export kubeconfig (LAN only)
  storage_seaweedfs/          chart, identities Secret, endpoint gate, probe
  storage_aistor/             operator, tenant, green gate, mc provisioning, probe
  storage_external/           validate + verify an unmanaged store
  s3_credentials/             publish (by storage) / resolve (for downstream)
  mc_helpers/                 reap leftover throwaway mc pods
  cnpg/                       operator, cluster + WAL to S3, scheduled backup
  quickwit/                   metastore URI, S3 secret, chart, seed index, ingress
  vector/                     aggregator: HTTP source, remap, disk buffer, sink
  grafana/                    chart + Quickwit datasource plugin + ingress
  grafana_mcp/                service account token, chart, ingress
  quickwit_mcp/               native MCP server, optional BasicAuth, ingress
  verify/                     readiness, version assert, WAL, direct + Vector log paths
```

## Hard-won gates encoded in the automation

These are the "exists ≠ ready" traps the playbook waits on, each learned the
hard way:

- **Operator webhooks lag their Deployment.** CNPG first-CR applies use
  `helm wait` + retries so the admission webhook is live before the CR lands.
- **A chart's `appVersion` is not the version you get.** Quickwit's chart still
  declares 0.8.2; only `image.tag` puts 0.9 on the node, so `verify` asks the
  running cluster and fails if the answer is wrong.
- **"Installed" is not "new enough".** `k8s_tooling` compares helm's version
  against the pin instead of checking mere presence, downloads to a
  version-keyed path (a fixed filename made `get_url` skip and silently reuse
  the old tarball), and asserts the result afterwards.
- **AIStor health gate.** The tenant must report `healthStatus: green` before
  buckets are usable; `volumesPerServer: 1` keeps a single-disk node green. The
  playbook polls the ObjectStore CR and fails fast on `NotFound`.
- **Service naming/port drift.** Roles assert the Service they depend on exists
  before anything resolves it, and the contract pins the port per chart.
- **Bucket provisioning is probe-gated, not object-gated.** Idempotency comes
  from probing real bucket access with the app key, then provisioning only on
  failure — not from any one-shot provisioning object. `mc ready` is the
  readiness check (the root user is denied list-all on newer charts).
- **Interrupted runs leave state.** `kubectl run --rm` only deletes the pod
  while the client stays attached, so a dropped SSH session leaves a
  fixed-name pod that makes every retry fail with `AlreadyExists`. Reaping runs
  before each `mc` step.
- **Ingest acceptance is not searchability.** `verify` pushes a log *and reads
  it back*, which is what catches a broken S3 endpoint or a bad metastore URI.
  The Vector path additionally proves the transform ran, by sending the wrong
  field names on purpose.
- **Strong root credentials.** The AIStor chart rejects `minio/minio123`; the
  root user is a role default and the password must be strong.
- **CNPG `barmanObjectStore` is deprecated (1.26).** Migrate to the Barman Cloud
  Plugin before CNPG 1.30 if this outlives a few upgrades.

## Verification

`roles/verify` is the acceptance test for the whole lab and is worth running on
its own (`-t verify`) after any change:

1. All Quickwit, Grafana and Vector pods Ready.
2. The running engine really is the pinned Quickwit version.
3. WAL objects have reached the backup bucket.
4. The seed index exists.
5. **Direct path** — push NDJSON to Quickwit's ingest endpoint with
   `commit=force`, then search it back out.
6. **Vector path** — push a log to Vector using *deliberately wrong* field
   names (`message`/`level`/`service`/`hostname`), then search Quickwit for the
   *normalised* name. A hit proves transport **and** transformation; the
   assertion that follows checks the severity was upper-cased. This path cannot
   use `commit=force` (Vector's sink URI carries no query string), so the retry
   window is sized past Quickwit's commit timeout plus Vector's batch timeout.
7. Grafana answers on both the Traefik path and the NodePort, and its
   provisioned datasource passes its own health check against Quickwit.
8. Both MCP servers answer, and the Grafana one completes a real MCP
   `initialize` handshake with the caller token.

## CI

`.github/workflows/ci.yml` runs on every push:

- **yamllint** + **ansible-lint** (`production` profile)
- **`ansible-playbook site.yml --syntax-check`** — catches undefined vars and
  unresolved dynamic role names
- **`ci/render_templates.py`** — renders every Jinja template against **every**
  storage backend contract with `StrictUndefined`, asserts valid YAML/JSON, and
  fails if a template hardcodes a value the contract owns (e.g. an S3 port) or a
  contract hardcodes its scheme
- **gitleaks** over the full history

CI has no inventory or secrets (both gitignored), so it materialises throwaway
ones from the committed `.example` files — which also proves a fresh clone has
everything it needs to run.

Run the same checks locally before pushing:

```bash
yamllint . && ansible-lint && ansible-playbook site.yml --syntax-check \
  && python3 ci/render_templates.py
```

> `ci/render_templates.py` uses **plain Jinja2**, not Ansible's templating, so
> Ansible-only filters (`regex_replace`, `password_hash`, ...) are unavailable
> in any template it renders. Stick to core Jinja filters in `.j2` files.
