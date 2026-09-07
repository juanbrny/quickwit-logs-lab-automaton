# BYOC Logs Lab — detailed reference

> This is the long-form documentation. For a quick overview and the fastest
> path to a running lab, start with the main [README](README.md).

Automates a full single-node BYOC Logs lab: k3s, the Datadog agent, an S3
object store (in-cluster or external), a PostgreSQL metastore via CloudNativePG
with WAL archiving, the CloudPrem (BYOC Logs) chart, Traefik ingress, and an
end-to-end log-push verification.

> **Lab only.** Single node, local storage, no TLS by default. This is a
> reproducible test/demo environment, not a production posture.

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
   | `datadog_api_key` | always | Datadog > Organization Settings > API Keys. Used by the agent and BYOC Logs. |
   | `s3_app_access_key` | `aistor`, `seaweedfs` | The app key you want the backend to **create** (not looked up — invent it, ~20 chars). CNPG (WAL) and BYOC Logs (indexes) use it. |
   | `s3_app_secret_key` | `aistor`, `seaweedfs` | Its ~40-char secret half. |
   | `aistor_license` | `s3_backend: aistor` | Free single-node eval licence from AIStor (min.io). |
   | `aistor_root_password` | `s3_backend: aistor` | Tenant root password, used only to provision buckets + the app key. **Must be strong** — newer charts reject the weak `minio/minio123` pair. The root *user* is `byocadmin` (a role default, not `minio`). |
   | `seaweedfs_admin_secret_key` | `s3_backend: seaweedfs` | Admin identity secret, used only to create buckets. |
   | `external_s3_access_key` / `_secret_key` | `s3_backend: external` | Credentials issued **by** the external store; validated and forwarded, not created. |

   `site.yml` stops if the file is missing and refuses to run while any
   `CHANGE_ME` remains for the active backend.

3. **Run:**

   ```bash
   ansible-playbook site.yml --ask-vault-pass
   ```

## Re-runs / partial runs

Everything is idempotent — `helm upgrade --install` semantics, `k8s`-module
applies, and bucket provisioning is gated behind a live bucket-access probe
(the `mc` provisioning pod runs only when the probe fails). Tags:

```bash
ansible-playbook site.yml -t distro          # k3s only
ansible-playbook site.yml -t storage         # the S3 backend only
ansible-playbook site.yml -t database        # CNPG only
ansible-playbook site.yml -t byoc,verify     # redeploy BYOC + smoke test
```

### Usage hints

- **After a mid-run failure**, just re-run the whole playbook — idempotency
  means completed stages are no-ops. Use a tag only when you know the failure
  was contained to one stage.
- **Working on the cluster by hand:** SSH to the node and use its tooling —
  `sudo kubectl get pods -A`, `sudo helm list -A`. The kubeconfig is
  `/etc/rancher/k3s/k3s.yaml` (root-readable), which is why `sudo` is needed.
- **Switching storage backends on a live lab** is a redeploy of the stack, not
  an in-place migration: flip `s3_backend`, then re-run `-t storage,database,byoc,verify`.
  Indexes and WAL from the old backend are not migrated.
- **Throwaway pods** (`mc-verify`, `mc-mkbucket`, `mc-accesskey`) are `--rm`;
  if a run is interrupted they can linger. Clean with
  `kubectl -n <s3_namespace> delete pod mc-verify mc-mkbucket mc-accesskey --ignore-not-found`.

## Version pinning (stable base)

Every external version — k3s, helm, all five Helm charts, and the `mc` image —
is pinned in **one file**, `group_vars/all/versions.yml`. Nothing else uses
`:latest` or an unpinned chart, and preflight **fails** if it finds a floating
tag or an unfilled placeholder. This is what stops a chart changing under you at
deploy time — a real problem this repo hit repeatedly (AIStor removed default
credentials, moved its S3 service from `:9000` to `:80`, and renamed the
service, all in one chart generation).

The current pinned baseline (known-good on 2026-07-17):

| Component | Chart | Version |
|---|---|---|
| k3s | — | `v1.35.6+k3s1` |
| aistor-operator | `minio/aistor-operator` | `5.9.0` |
| aistor-objectstore | `minio/aistor-objectstore` | `1.0.16` |
| seaweedfs | `seaweedfs/seaweedfs` | `4.39.0` |
| cnpg | `cnpg/cloudnative-pg` | `0.29.0` |
| datadog-operator | `datadog/datadog-operator` | `2.24.0` |
| cloudprem | `datadog/cloudprem` | `0.4.5` |

**Upgrading a component — one at a time:** bump exactly one line in
`versions.yml`, run the relevant tag, verify. If it breaks, revert that one line
— you know precisely what changed. Same discipline already applied to k3s.

For a truly immutable base, pin `mc_image` by digest (`mc@sha256:...`) rather
than tag; chart versions are immutable once published, so a number suffices
there.

## Swapping variants

Two selectors in `group_vars/all/main.yml`:

| Variable | Maps to |
|---|---|
| `k8s_distro` | `roles/<distro>` — must install k8s, wait Ready, (for the LAN model) export the kubeconfig, and satisfy the distro contract (`ingress_flavor`, `default_storage_class`) |
| `s3_backend` | `roles/storage_<backend>` + `vars/storage_<backend>.yml` — must provision buckets, provision or accept an app key scoped to `s3_buckets`, and publish the contract |

**Ingress flavor:** `byoc_logs` includes `tasks/ingress_<flavor>.yml`. To support
an nginx-based distro, add `ingress_nginx.yml` and set `ingress_flavor: nginx`.

## Preflight

`roles/preflight` runs on the node, from gathered facts, **before k3s is
installed** — an undersized VM fails in seconds instead of after a long run
ending in Pending pods.

```yaml
preflight_required_vcpu: 20
preflight_required_ram_gb: 64
preflight_required_disk_gb: 100
```

Raw OS values (`ansible_processor_vcpus`, `ansible_memtotal_mb`, `df`). RAM gets
5% slack because `/proc/meminfo` reports a little under nominal (firmware/kernel
reserve) — a 64 GB VM shows ~62-63 GB; the check catches the wrong VM size, not
bytes. Preflight also asserts every version in `versions.yml` is pinned.

Override in `group_vars/all/main.yml`; bypass with `-e preflight_skip=true`. Run
alone with `-t preflight`.

## Credential flow

Storage backends **publish** the S3 app credentials; downstream roles
**consume** them. The direction matters because backends differ in who picks the
keys:

| Backend | Who chooses the credentials |
|---|---|
| `aistor`, `seaweedfs` | **You do.** `s3_app_access_key` / `s3_app_secret_key` from the vault are an *input*; the backend creates that key and scopes it to `s3_buckets`. |
| `external` | **The other system does.** `external_s3_access_key` / `_secret_key` are issued elsewhere; the playbook validates and forwards them. |
| a future `rook` | **Ceph does.** It generates them and returns a Secret; the role would read and publish them. |

Every storage role ends by calling `s3_credentials/publish`, which sets facts
for this run **and** writes Secret `byoc-s3-credentials` into the storage
namespace. Downstream (`cnpg`, `byoc_logs`, `verify`) never reads `s3_app_*` or
`external_s3_*` — only the published `s3_access_key` / `s3_secret_key`.

That Secret is why partial runs work: `-t byoc` on an existing deployment
resolves the credentials from it (`s3_credentials/main`) without re-running the
storage role. If neither the facts nor the Secret exist, you get a clear failure
instead of an undefined-variable trace.

## Storage backends

`s3_backend` selects both the role and the contract file. Shipped: `aistor`,
`seaweedfs`, `external`. The contract (`vars/storage_<backend>.yml`) is what
downstream roles consume — they never reference a backend by name:

| key | aistor | seaweedfs | external |
|---|---|---|---|
| `s3_namespace` | `primary-object-store` | `seaweedfs` | `byoc-s3` (mc pods + creds Secret only) |
| `s3_scheme` | http | http | usually **https** |
| `s3_host` | in-cluster service FQDN | in-cluster service FQDN | whatever you point it at |
| `s3_port` | **80** | **8333** | 443 |
| `s3_force_path_style` | true | true | true (false for real AWS S3) |

`s3_endpoint` is always derived as `{scheme}://{host}:{port}` — CI fails any
contract that hardcodes it. `s3_service_name` is **backend-internal** (aistor
and seaweedfs use it to build the FQDN and assert the Service exists); external
has no k8s Service, so it isn't in the contract.

> **AIStor port note:** the pinned `aistor-objectstore` chart (`1.0.16`) exposes
> the S3 API on **port 80** (NodePort 31001), not 9000. The contract reflects
> this. Older charts used 9000 — another reason the version is pinned.

### Inputs vs contract

Each backend takes its own `<backend>_s3_*` **inputs** from
`group_vars/all/main.yml` and maps them onto the neutral `s3_*` **contract**
keys, so the knob you reach for doesn't depend on which backend is active:

```yaml
aistor_s3_scheme: http      # -> s3_scheme
seaweedfs_s3_scheme: http   # -> s3_scheme
external_s3_scheme: https   # -> s3_scheme
```

The scheme is not cosmetic — it drives the deployment. `aistor_s3_scheme: https`
sets `disableAutoCert: false` so the operator issues a self-signed cert; pair it
with `s3_tls_skip_verify: true` so `mc` (which every probe runs) gets
`--insecure`. SeaweedFS **asserts** `http` (its TLS wiring isn't implemented
here). CI fails any contract that hardcodes a scheme.

### How aistor and seaweedfs differ

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
`requireSafeReplicaSize: false`), and it competes with the BYOC pods for RAM.
Provision Ceph separately and consume its RGW endpoint through `external` — same
contract, none of the coupling.

### Adding a backend

1. `roles/storage_<name>/` — deploy it, gate on ready, provision buckets and a
   key scoped to `s3_buckets`, then call `s3_credentials/publish` with whatever
   credentials it ended up with.
2. `vars/storage_<name>.yml` — publish the contract (`s3_namespace`, `s3_scheme`,
   `s3_host`, `s3_port`, `s3_endpoint`, `s3_endpoint_external`,
   `s3_force_path_style`). CI fails if a key is missing.
3. Set `s3_backend: <name>`. Nothing else changes.

## Layout

```
site.yml                      play 1: node prep + k8s distro (on the node)
                              play 2: the stack (on the node by default)
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
  datadog_agent/              operator + DatadogAgent (self-monitoring)
  storage_aistor/             operator, tenant, green gate, mc provisioning, probe
  storage_seaweedfs/          chart, identities Secret, endpoint gate, probe
  storage_external/           validate + verify an unmanaged store
  s3_credentials/             publish (by storage) / resolve (for downstream)
  cnpg/                       operator, cluster + WAL to S3, scheduled backup
  byoc_logs/                  secrets, metastore URI, CloudPrem chart, ingress
  verify/                     pod readiness, WAL check, test log push
```

## Hard-won gates encoded in the automation

These are the "exists ≠ ready" traps the playbook waits on, each learned the
hard way:

- **Operator webhooks lag their Deployment.** Datadog/CNPG first-CR applies use
  `helm wait` + retries so the admission webhook is live before the CR lands.
- **AIStor health gate.** The tenant must report `healthStatus: green` before
  buckets are usable; `volumesPerServer: 1` keeps a single-disk node green. The
  playbook polls the ObjectStore CR and fails fast on `NotFound`.
- **Service naming/port drift.** The role asserts the S3 Service exists before
  anything resolves it, and the contract pins port 80 for the current chart.
- **Bucket provisioning is probe-gated, not object-gated.** Idempotency comes
  from probing real bucket access with the app key, then provisioning only on
  failure — not from any one-shot provisioning object. `mc ready` is the
  readiness check (the root user is denied list-all on newer charts).
- **Strong root credentials.** The AIStor chart rejects `minio/minio123`; the
  root user is `byocadmin` and the password must be strong.
- **CNPG `barmanObjectStore` is deprecated (1.26).** Migrate to the Barman Cloud
  Plugin before CNPG 1.30 if this outlives a few upgrades.

## CI

`.github/workflows/ci.yml` runs on every push:

- **yamllint** + **ansible-lint** (`production` profile)
- **`ansible-playbook site.yml --syntax-check`** against both the default and
  SSM inventories — catches undefined vars and unresolved dynamic role names
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
