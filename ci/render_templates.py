#!/usr/bin/env python3
"""Render every Jinja template against every storage backend contract.

Catches, without a cluster: undefined vars, malformed YAML/JSON output, and
templates that silently hardcode a value the contract is supposed to own
(e.g. an S3 port of 9000 when the active backend listens on 8333).
"""
import glob
import json
import sys

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_VARS = yaml.safe_load(open("group_vars/all/main.yml"))
BACKENDS = [p.split("storage_")[1][:-4] for p in glob.glob("vars/storage_*.yml")]

BASE = dict(
    # group_vars derives several values from inventory structure; stub it.
    groups={"lab_node": ["ci-node"], "k8s_control": ["ci-node"]},
    k8s_control_on_node=True,
    kubeconfig_path="/etc/rancher/k3s/k3s.yaml",
    node_ip="10.0.0.10",
    lab_node_name="ci-node",
    playbook_dir=".",
    grafana_admin_password="ci", grafana_mcp_caller_token="ci",
    aistor_license="ci",
    # pinned in group_vars/all/versions.yml at runtime
    quickwit_image_tag="v0.9.0",
    grafana_quickwit_plugin_version="0.6.0",
    grafana_quickwit_plugin_url="https://example.invalid/plugin.zip",
    # inputs to managed backends
    s3_app_access_key="CIACCESSKEY0000000AB",
    s3_app_secret_key="cisecretkey000000000000000000000000000AB",
    # published by every backend (facts at runtime)
    s3_access_key="CIACCESSKEY0000000AB",
    s3_secret_key="cisecretkey000000000000000000000000000AB",
    aistor_root_password="ci", seaweedfs_admin_secret_key="ci",
    s3_tls_skip_verify=False, mc_opts="",
    aistor_s3_scheme="http", seaweedfs_s3_scheme="http", external_s3_scheme="https",
    # external backend
    external_s3_host="s3.example.com", external_s3_port=443,
    external_s3_force_path_style=True, external_s3_namespace="lab-s3",
    external_s3_access_key="CIEXTKEY00000000000A",
    external_s3_secret_key="ciextsecret0000000000000000000000000000A",
)


def role_defaults(role):
    try:
        return yaml.safe_load(open(f"roles/{role}/defaults/main.yml")) or {}
    except FileNotFoundError:
        return {}


def resolve(env, mapping):
    """group_vars/contract values are themselves Jinja — resolve to fixpoint."""
    for _ in range(10):
        changed = False
        for k, v in list(mapping.items()):
            if isinstance(v, str) and "{{" in v:
                new = env.from_string(v).render(**mapping)
                if new != v:
                    mapping[k], changed = new, True
        if not changed:
            break
    return mapping


def main():
    env = Environment(loader=FileSystemLoader("."), undefined=StrictUndefined)
    failures = []

    for backend in sorted(BACKENDS):
        contract = yaml.safe_load(open(f"vars/storage_{backend}.yml"))
        v = {**REPO_VARS, **BASE, **contract}
        for role in ("storage_" + backend, "quickwit", "grafana", "grafana_mcp", "cnpg",
                     "k3s", "verify", "preflight"):
            v = {**role_defaults(role), **v}
        v = resolve(env, v)

        print(f"\n--- backend: {backend}  ({v['s3_endpoint']})")

        # every contract must publish the same required keys
        required = ["s3_namespace", "s3_scheme", "s3_host", "s3_port",
                    "s3_endpoint", "s3_endpoint_external", "s3_force_path_style"]
        for key in required:
            if key not in {**contract}:
                failures.append(f"{backend}: contract is missing {key}")

        # the contract must not be silently bypassed by a hardcoded endpoint
        if f":{v['s3_port']}" not in v["s3_endpoint"]:
            failures.append(f"{backend}: s3_endpoint does not use s3_port")
        if not v["s3_endpoint"].startswith(v["s3_scheme"] + "://"):
            failures.append(f"{backend}: s3_endpoint does not use s3_scheme")

        # the scheme must be an input, not a literal baked into the contract
        raw = open(f"vars/storage_{backend}.yml").read()
        if "s3_scheme: http" in raw:
            failures.append(
                f"{backend}: contract hardcodes s3_scheme — derive it from "
                f"{backend}_s3_scheme so the knob exists for every backend")

        paths = glob.glob("roles/*/templates/*.j2") + glob.glob("templates/*.j2")
        for path in sorted(paths):
            # a role's templates only render under its own backend
            if "/storage_" in path and f"/storage_{backend}/" not in path:
                continue
            try:
                out = env.get_template(path).render(**v)
                if path.endswith(".json.j2"):
                    json.loads(out)
                    kind = "JSON ok"
                elif path.endswith(".sh.j2"):
                    kind = "shell ok"
                else:
                    yaml.safe_load(out)
                    kind = "YAML ok"
                # a template that bakes in another backend's port is a bug
                bad_port = "9000" if str(v["s3_port"]) != "9000" else "8333"
                if bad_port in out and "s3_port" not in open(path).read():
                    failures.append(f"{backend}: {path} hardcodes port {bad_port}")
                print(f"    {path}: {kind}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{backend}: {path}: {exc}")
                print(f"    {path}: FAIL — {exc}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\nAll templates render for all backends.")


if __name__ == "__main__":
    main()
