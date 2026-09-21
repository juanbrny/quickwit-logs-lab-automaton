# Concepts — what each piece does and why it is here

A primer for anyone who has not worked with Quickwit, Vector or MCP before.
It explains the technology, not this repository's configuration; for that, see
[README-detailed.md](../README-detailed.md).

- [The problem this stack solves](#the-problem-this-stack-solves)
- [Quickwit](#quickwit)
- [Why an object store, and why that matters for cost](#why-an-object-store-and-why-that-matters-for-cost)
- [The metastore, and why it is PostgreSQL](#the-metastore-and-why-it-is-postgresql)
- [Vector](#vector)
- [k3s and Traefik](#k3s-and-traefik)
- [MCP — letting an LLM read the logs](#mcp--letting-an-llm-read-the-logs)
- [Following one log line end to end](#following-one-log-line-end-to-end)
- [Glossary](#glossary)

## The problem this stack solves

Logs are enormous, written once, read rarely, and searched unpredictably. A
classic search engine keeps its index on fast local disks attached to the same
machines that serve queries. That works, but it ties three things together that
grow at very different rates: how much data you keep, how much compute you need
to search it, and how much you must pay to have both switched on at all times.
Keeping ninety days of logs means paying for ninety days of hot disk, and for
the servers attached to it, whether or not anyone searches.

Quickwit breaks that link. The index lives in an object store, and the machines
that search it hold nothing permanent. You can turn search capacity up and down
without moving data, and you pay object-store prices for retention instead of
block-storage prices.

## Quickwit

Quickwit is an open-source search engine built specifically for append-only
data such as logs and traces. Two design choices matter most.

**The index is a set of immutable files in object storage.** Each file is
called a **split**. A split is a self-contained, compressed, searchable chunk
of documents — the inverted index, the column store and the metadata for those
documents, all in one object. Splits are never edited. New data creates new
splits; old data is deleted by dropping whole splits.

**Search reads directly from the object store.** A searcher does not need a
local copy of the index. It works out which byte ranges of which splits it
needs and fetches exactly those, caching what is worth caching. This is why
searchers are stateless and can be scaled or replaced freely.

Quickwit runs as a set of cooperating services. In this lab each is its own
Kubernetes workload:

| Service | What it does |
|---|---|
| **indexer** | Receives documents, builds splits, uploads them to the object store, and records them in the metastore. |
| **searcher** | Answers queries. Fetches the split ranges it needs from the object store. Holds no permanent state. |
| **control plane** | Decides which indexer handles which indexing pipeline, and reacts when one disappears. |
| **metastore** | The service that reads and writes index metadata. In this lab it is backed by PostgreSQL. |
| **janitor** | Background housekeeping: merging small splits into bigger ones, and deleting splits past their retention. |

The **merge** work the janitor does matters more than it sounds. Indexing
produces many small splits, and searching many small splits is slow. Merging
consolidates them into fewer, larger ones. It costs CPU and object-store
traffic, which is why a busy cluster is doing significant work even when nobody
is querying.

### The index and its doc mapping

An **index** is a named collection of splits plus the rules for interpreting
documents — the **doc mapping**. This lab creates one index, `lab-logs`.

The doc mapping decides which fields are typed and how. A field can be stored
as text for full-text search, as a **fast field** (a column store, which is
what makes aggregations and sorting quick), or simply kept without being
indexed. Fields not named in the mapping are still accepted, because the
mapping runs in `dynamic` mode, but they are stored untyped.

That last point is the single most common source of confusion. Under `dynamic`
mode a misspelled or unexpected field name does not raise an error — the
document is accepted and the field is quietly stored in a generic way, so later
searches for it return nothing. A silent miss, not a failure. This is exactly
why Vector normalises field names before documents reach Quickwit, and why the
`verify` role deliberately sends the *wrong* names and checks they were
rewritten.

The five typed fields in this lab and the reasoning behind each are in
[The index](../README-detailed.md#the-index).

## Why an object store, and why that matters for cost

Object storage (the S3 API) is cheap, effectively unlimited, and replicated by
someone else. It is also slow to first byte compared with a local NVMe drive —
tens of milliseconds rather than tens of microseconds.

Quickwit's design accepts that latency and works around it: splits are laid out
so that answering a query needs a small number of large, predictable reads
rather than many small random ones. The result is a system where retention cost
falls dramatically, and query latency is good enough for log investigation
(seconds, not milliseconds).

This is the trade-off to understand before you benchmark the lab. Quickwit is
not trying to beat a hot-disk search engine on raw latency. It is trying to
make keeping a year of logs affordable, and searching that year possible.

The lab lets you swap the store — `seaweedfs`, `aistor`, or an `external` S3 —
precisely so you can see how much of the behaviour is Quickwit and how much is
the storage underneath it.

## The metastore, and why it is PostgreSQL

Splits are immutable, but the *list* of splits is not. Something must record
which splits exist, which index they belong to, what time range each covers,
which are being merged, and where indexing had got to. That is the
**metastore**.

It must support transactions and concurrent writers, which object storage does
not. Quickwit can keep metadata in a file for single-node use, but any real
deployment uses PostgreSQL. This lab uses PostgreSQL from the start, so the
lab's shape matches a real deployment rather than a toy one.

**CloudNativePG** is the Kubernetes operator that runs that PostgreSQL: it
creates a primary and replicas, handles failover, and — importantly here —
continuously archives the write-ahead log (WAL) to the object store. That WAL
archive is what makes point-in-time recovery possible.

Worth internalising: **the object store holds your logs, and PostgreSQL holds
the map to them.** Lose the object store and you lose the data. Lose PostgreSQL
without a backup and the data still exists but nothing knows how to find it.
This is why the metastore gets a three-replica cluster and WAL archiving in
what is otherwise a single-node lab.

## Vector

[Vector](https://vector.dev) is a pipeline for observability data: it takes
events in through **sources**, reshapes them with **transforms**, and sends
them out through **sinks**.

Vector is most often run as an **agent** — one per machine, tailing container
logs. **That is not how it is used here.** In this lab Vector runs as an
**aggregator**: a service that sits in the middle of the network and receives
logs over HTTP from whatever is producing them. It collects nothing by itself.

Two reasons it is in the path.

**Backpressure.** The sink writes to a disk buffer before forwarding to
Quickwit. When the indexer is slow, restarting, or catching up, events spill to
that buffer instead of being lost. When the buffer fills, Vector is configured
to **block** — it stops accepting new writes and the sender sees a slow or
refused request. That sounds worse than the alternative, and it is deliberately
chosen: the alternative (`drop_newest`) keeps producers fast by throwing logs
away. In front of an indexer, a sender that is told to slow down is far better
than a log that silently disappears.

This is also the single most useful thing to watch during a load test. Buffer
depth growing means the indexer is not keeping up — and it shows that *before*
any CPU graph moves.

**Transformation.** A `remap` transform, written in Vector's own VRL language,
renames incoming fields onto the index's schema: `message` becomes `body`,
`level` becomes an upper-cased `severity_text`, `service` becomes
`service_name`, `hostname` becomes `host`. Every sender keeps its own format
and the translation lives in one place. Without this, each sender would have to
know the index schema, and — because of `dynamic` mode above — getting it
subtly wrong would fail silently.

The buffer needs somewhere durable to live, which is why Vector is deployed as
a StatefulSet with a persistent volume rather than a plain Deployment.

## k3s and Traefik

**k3s** is a small, single-binary Kubernetes distribution. It is here because
every component in this stack ships as a Helm chart, and k3s gives you a real
Kubernetes cluster on one VM in about thirty seconds.

**Traefik** ships inside k3s and acts as the ingress: a single entry point on
port 80 that routes by URL path — `/vector` to Vector, `/grafana` to Grafana,
`/ui` and `/api/v1` to Quickwit, and so on. Each component is *also* published
on a fixed NodePort, so you can bypass the ingress when you want to test a
component directly, or when the ingress itself is what you suspect.

One Traefik behaviour is worth knowing in advance because it causes a confusing
failure: routes have **priorities**, and when two routes match the same path at
the same priority, Traefik breaks the tie by router name. If a leftover
deployment from an earlier lab still claims `/`, your requests can silently go
to the wrong cluster. See
[troubleshooting](troubleshooting.md#requests-reach-the-wrong-cluster).

## MCP — letting an LLM read the logs

The **Model Context Protocol** is a standard way to give a language model a set
of tools it can call. An MCP server exposes tools with names, descriptions and
typed arguments; an MCP client — Claude, Bionic, or any other — discovers them
and decides when to call them. The model never sees a database or an API key;
it sees a list of things it is allowed to do.

For logs this is a meaningful change in how investigation works. Instead of
writing the query yourself, you describe what you are looking for, and the model
issues the searches, reads the results, and narrows down. The logs stay where
they are — the model calls in, no data is exported.

Two servers are deployed side by side because they are good at different
things:

- **Grafana MCP** talks to Grafana, which talks to Quickwit. It returns
  documents, up to 100 per call, and no aggregations. It authenticates callers
  with a bearer token.
- **Quickwit MCP** talks to Quickwit directly and can run **aggregations** —
  counts over time, grouping by field.

The aggregation gap is the practical difference. "When did this start
happening?" over twelve months of logs is one histogram call on the native
server, but a series of bisecting document searches through Grafana's. If you
only try one, try the native one.

> The native Quickwit MCP server has **no authentication of its own** at the
> version pinned here. It is read-only, but anyone who can reach the port can
> read every log. The lab can put HTTP basic auth in front of it; see
> [MCP servers](../README-detailed.md#mcp-servers--llm-access-to-the-logs).

## Following one log line end to end

Putting it together, with the `curl` from the README:

1. **You POST** a JSON line to `http://<node>/vector`.
2. **Traefik** matches the `/vector` prefix and forwards to Vector's service.
3. **Vector's `http_server` source** accepts it on any path, decodes the JSON,
   and stamps an arrival `timestamp` unless the event carried its own.
4. **The `remap` transform** renames the fields onto the index schema.
5. **The sink** batches the event with others, writes the batch through the
   disk buffer, and POSTs NDJSON to the Quickwit indexer's ingest API.
6. **The indexer** accepts the documents into an indexing pipeline. After a
   commit interval it builds a **split**, uploads it to the object store, and
   records it in the **metastore**.
7. **Your search** arrives at a **searcher**, which asks the metastore which
   splits could contain matching documents in that time range, fetches the
   relevant byte ranges from the object store, and merges the results.
8. **The janitor**, later, merges that small split into a larger one, and
   eventually deletes it when retention expires.

Steps 5 and 6 are why a document is not searchable the instant it is accepted.
A write returns as soon as it is durable, but it becomes visible to search only
once a split is published — the commit interval, plus the batching delay in
Vector. That gap is normal, and it is why the lab's Vector test waits up to two
minutes rather than checking immediately.

## Glossary

| Term | Meaning |
|---|---|
| **Split** | An immutable, self-contained, searchable file in the object store. The unit Quickwit indexes, merges and deletes. |
| **Doc mapping** | The rules that say how fields in an incoming document are indexed and stored. |
| **Dynamic mode** | Fields not named in the doc mapping are accepted and stored untyped, rather than rejected. Convenient, and a silent trap. |
| **Fast field** | A field stored column-wise so aggregations and sorting are quick. |
| **Metastore** | The transactional record of which splits exist and what they contain. PostgreSQL here. |
| **Commit interval** | How long the indexer collects documents before publishing a split. Sets the delay before new logs are searchable. |
| **Merge** | Background consolidation of many small splits into fewer large ones. |
| **Aggregator (Vector role)** | Vector running as a central service receiving pushed logs, rather than as a per-host agent. |
| **Backpressure** | Telling the sender to slow down instead of discarding data when the pipeline is full. |
| **VRL** | Vector Remap Language — the small expression language used in `remap` transforms. |
| **NDJSON** | Newline-delimited JSON: one complete JSON document per line. The format Quickwit ingests. |
| **WAL archiving** | Continuously copying PostgreSQL's write-ahead log to the object store, enabling point-in-time recovery. |
| **MCP** | Model Context Protocol — a standard for exposing callable tools to a language model. |
