# Troubleshooting

Real failures, with the command that diagnoses each one. Ordered roughly by
when you hit them.

- [Getting to the node](#getting-to-the-node)
- [Things that fail during deployment](#things-that-fail-during-deployment)
- [Things that fail in `verify`](#things-that-fail-in-verify)
- [Requests reach the wrong cluster](#requests-reach-the-wrong-cluster)
- [Logs go in but do not come out](#logs-go-in-but-do-not-come-out)
- [Running commands on the node by hand](#running-commands-on-the-node-by-hand)
- [Changing the automation](#changing-the-automation)
- [Starting over](#starting-over)

Throughout, `<node>` is your lab node's address, and ad-hoc commands are shown
as `ansible <your-node-name> -m shell -a '...'`.

---

## Getting to the node

### `UNREACHABLE` in the middle of a long task

Several roles run `helm --wait` with timeouts up to 600 seconds, during which
the SSH channel is completely silent. Anything with an idle timeout in the path
— NAT, VPN, `sshd` — closes it, and Ansible reports the host unreachable even
though the work on the node succeeded.

`ansible.cfg` already sets `ServerAliveInterval` and a matching
`ControlPersist`. If you still see it, the timeout is shorter than those
settings somewhere in your path; lower `ServerAliveInterval`.

Re-running is safe — everything is idempotent.

### `certificate is not yet valid` while the node downloads something

The node's clock is wrong. TLS rejects a certificate whose validity window has
not started yet, which is what a clock running *behind* real time produces.

```bash
ansible <node> -m shell -a 'chronyc tracking' -b
ansible <node> -m shell -a 'chronyc makestep' -b
```

### Connection refused to port 6443

Your inventory has `k8s_control` pointing at `localhost` while the cluster is
on the remote node, so Ansible is trying to reach an API server on your laptop.

In the default (on-node) model, `lab_node` and `k8s_control` must be the **same
host** — the shipped template uses a YAML anchor so they cannot drift. Start
from `inventory/hosts.yml.example` again rather than editing by hand.

---

## Things that fail during deployment

### A chart fails to render with an unknown template function

The helm binary on the node is older than the chart requires. `k8s_tooling`
pins helm and compares versions rather than just checking that helm exists, so
this means the pin itself is too old:

```bash
ansible <node> -m shell -a 'helm version'
```

Raise `helm_version` in `group_vars/all/versions.yml` and re-run with
`-t tooling`.

### `chart "<name>" matching <version> not found`

The version pinned in `group_vars/all/versions.yml` is not published. This
usually happens when a version was read from a chart repository's `main`
branch, where `Chart.yaml` is often bumped ahead of the release.

Check what is actually published:

```bash
ansible <node> -m shell -a 'helm repo update >/dev/null && helm search repo <repo>/<chart> --versions | head'
```

### `pods "mc-..." already exists`

`kubectl run --rm` only deletes the pod while the client stays attached. An
interrupted run — a dropped SSH session, a `Ctrl-C` — leaves the fixed-name pod
behind, and every retry then fails.

The playbook reaps these before each `mc` step, so re-running normally clears
it. To do it by hand:

```bash
ansible <node> -m shell -a 'kubectl delete pod -n <s3-namespace> mc-ls-wal --ignore-not-found'
```

### The AIStor tenant never goes green

A single-disk node needs `volumesPerServer: 1`, and the chart rejects weak root
credentials such as `minio123`. Check the ObjectStore CR:

```bash
ansible <node> -m shell -a 'kubectl get objectstore -A -o wide'
```

### A pod is `Pending` forever

Usually the node cannot satisfy a PVC or a resource request.

```bash
ansible <node> -m shell -a 'kubectl get pvc -A'
ansible <node> -m shell -a 'kubectl describe pod -n <ns> <pod> | tail -25'
```

---

## Things that fail in `verify`

### `Expected Quickwit 0.9.x but the cluster reports ...`

Two very different causes, and the reported version tells you which.

**It reports `0.8.x`.** The chart's `appVersion` is 0.8.2 and only
`quickwit_image_tag` puts 0.9 on the node. The tag did not take effect —
re-run with `-t quickwit`.

**It reports something unrelated** (a version number you do not recognise).
Your request is not reaching this Quickwit at all. Go to
[Requests reach the wrong cluster](#requests-reach-the-wrong-cluster).

### `error: no matching resources found` on a `kubectl wait`

The namespace has no pods — this is not a timeout, and you will notice it
failed in well under a second. The component was never deployed, or was
removed.

This happens most often after a partial run: `verify` checks the **whole**
platform, so running `-t vector,verify` on a lab where Grafana was never
installed fails on Grafana, not on Vector.

```bash
ansible <node> -m shell -a 'kubectl get pods -A'
```

Either deploy the missing part, or run the full `ansible-playbook site.yml`.

### The Vector test times out

`verify` pushes a log to Vector with deliberately wrong field names and then
searches Quickwit for the normalised name. It waits up to two minutes, because
this path cannot force a commit.

If it fails, work out *which* half broke:

```bash
# 1. did Vector accept it?
ansible <node> -m shell -a "curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost/vector -H 'Content-Type: application/x-ndjson' -d '{\"message\":\"probe\",\"level\":\"info\",\"service\":\"probe-test\"}'"

# 2. is Vector forwarding, or is its buffer filling?
ansible <node> -m shell -a 'kubectl logs -n vector vector-0 --tail=40'

# 3. did anything at all arrive?
ansible <node> -m shell -a "curl -s 'http://localhost/api/v1/lab-logs/search?query=service_name:probe-test'"
```

A `200` from step 1 with nothing in step 3 means the document reached Vector
but not Quickwit — check the sink in Vector's logs. A hit in step 3 that shows
`message` rather than `body` means the `remap` transform is not running.

### Grafana's datasource health check fails

The Quickwit datasource plugin refuses a datasource whose timestamp field has
no explicit `output_format` in the index doc mapping, and it needs a fast
log-level field. If you have edited the doc mapping, compare it with
[The index](../README-detailed.md#the-index).

The plugin is downloaded on Grafana's first boot, so the very first run needs
a little patience — `verify` already retries for two minutes.

---

## Requests reach the wrong cluster

**Symptom:** `verify` reports a version you do not recognise, or searches
return documents you never indexed, or ingest seems to work but nothing
appears.

**Cause:** Traefik routes by rule and priority. When two IngressRoutes match
the same path at the same priority, the tie is broken by router name —
alphabetically. A leftover deployment from an earlier lab that still claims
`PathPrefix(/)` will therefore win against a newer one whose name sorts later,
and it will silently swallow `/api/v1/*`, searches, and ingest.

**Diagnose:**

```bash
ansible <node> -m shell -a "kubectl get ingressroute -A -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,MATCH:.spec.routes[*].match,PRIO:.spec.routes[*].priority'"
```

Two rows matching `PathPrefix(\`/\`)` at the same priority is the bug.

**Fix.** Remove the leftover. The least destructive option is to delete only
its IngressRoute, which leaves its pods and data untouched:

```bash
ansible <node> -m shell -a 'kubectl delete ingressroute <name> -n <namespace>'
```

To remove the old stack entirely:

```bash
ansible <node> -m shell -a 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm uninstall <release> -n <namespace>
kubectl delete namespace <namespace>'
```

**Avoiding it:** if you are keeping two log stacks on one node deliberately,
give them different path prefixes or different hostnames rather than relying on
priorities.

---

## Logs go in but do not come out

### The write succeeded but the search finds nothing

**Give it time first.** A document becomes searchable only once the indexer
publishes a split — the commit interval plus, on the Vector path, the batching
delay. Ten to thirty seconds is normal. Direct ingest can skip the wait with
`?commit=force`:

```bash
curl -X POST 'http://<node>/api/v1/lab-logs/ingest?commit=force' \
  -H 'Content-Type: application/x-ndjson' \
  -d '{"timestamp":"2026-01-01T00:00:00Z","body":"forced","severity_text":"INFO","service_name":"demo"}'
```

**Then suspect the field names.** The index runs in `dynamic` mode, so a
document with unexpected field names is *accepted* and stored untyped — the
search simply misses. Nothing errors. Search for what you actually sent:

```bash
curl "http://<node>/api/v1/lab-logs/search?query=*" | head -c 600
```

If the returned documents show `message` where you expected `body`, the
normalisation did not happen — you bypassed Vector, or the transform is not
running.

### `503` from the ingest endpoint after a while

Usually the node is out of disk, which stops the indexer publishing splits.

```bash
ansible <node> -m shell -a 'df -h /'
ansible <node> -m shell -a 'kubectl logs -n quickwit quickwit-indexer-0 --tail=50'
```

### Bulk-loading history behaves oddly

Send backdated bulk loads **directly to Quickwit**, not through Vector. Vector
is a live pipeline: it stamps arrival time unless every record carries
`event_timestamp`, so replaying months of history through it rewrites your
timestamps to now.

---

## Running commands on the node by hand

### `helm` says `Kubernetes cluster unreachable: ... localhost:8080`

`helm` has no kubeconfig in a non-login shell. `kubectl` works because k3s's
binary falls back to `/etc/rancher/k3s/k3s.yaml` on its own; helm does not.

```bash
ansible <node> -m shell -a 'KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm list -A'
```

The playbook is unaffected — its helm tasks pass the kubeconfig explicitly.

### Useful one-liners

```bash
ansible <node> -m shell -a 'kubectl get pods -A'
ansible <node> -m shell -a 'KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm list -A'
ansible <node> -m shell -a "curl -s http://localhost/api/v1/version"
ansible <node> -m shell -a "curl -s 'http://localhost/api/v1/indexes' | head -c 400"
ansible <node> -m shell -a 'kubectl logs -n vector vector-0 --tail=50'
ansible <node> -m shell -a 'kubectl exec -n quickwit quickwit-indexer-0 -- wget -qO- localhost:7280/metrics | head -40'
```

---

## Changing the automation

### CI passed but the deploy failed on malformed YAML

`ci/render_templates.py` renders templates with plain Jinja2. Ansible's
`template` module does **not** use Jinja2's defaults — notably it sets
`trim_blocks: yes`, which removes the newline after a block tag. A line ending
in `{% endraw %}` therefore keeps its newline in CI and loses it in a real run,
gluing the next line onto it.

The renderer now sets `trim_blocks=True` to match. If you add another rendering
path, keep it in step with Ansible, or CI will quietly validate different bytes
than the deploy produces.

More generally: avoid nesting a Helm template inside a YAML block scalar inside
an Ansible template. If you need a Kubernetes object that a chart does not
provide, create it from the role with `kubernetes.core.k8s` instead of the
chart's `extraObjects` — that is the pattern the rest of this repository uses.

### A variable is undefined only in a partial run

Role defaults exist **only while that role runs**. If role A reads
`b_something` from role B's `defaults/`, it works in a full run and fails under
`-t a`. Anything shared belongs in `group_vars/all/main.yml`. See
[Variable scope](../README-detailed.md#variable-scope--a-rule-worth-knowing).

### Run the full check suite locally before pushing

```bash
yamllint . && ansible-lint && ansible-playbook site.yml --syntax-check \
  && python3 ci/render_templates.py
```

---

## Starting over

The playbook is idempotent, so re-running is almost always better than
rebuilding. When you do want a clean slate, uninstall the releases rather than
deleting namespaces first — a namespace stuck `Terminating` usually means an
operator's finalizer is still waiting on a custom resource.

```bash
ansible <node> -m shell -a 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm list -A'
```

To remove k3s entirely and start from bare metal, k3s ships its own uninstall
script on the node:

```bash
ansible <node> -m shell -a '/usr/local/bin/k3s-uninstall.sh' -b
```

That destroys everything, including all indexed logs and the object store.
