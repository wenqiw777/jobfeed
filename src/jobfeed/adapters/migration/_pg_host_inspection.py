"""Pure host-side Docker inspection verifier for restore bootstrap provenance."""

from __future__ import annotations

from typing import cast

from jobfeed.adapters.migration._pg_preprovisioned_types import (
    RestoreBootstrap,
    RestoreServiceBootstrap,
    bootstrap_sha256,
)

_INSPECTION_KEYS = {"network", "source", "scratch", "runner"}
_PROJECT_LABEL = "com.docker.compose.project"
_SERVICE_LABEL = "com.docker.compose.service"


def verify_host_inspection(
    bootstrap: RestoreBootstrap, pre_docs: object, post_docs: object
) -> str:
    """Verify host Docker inspection before and after the runner session.

    Args:
        bootstrap: Exact provenance document bound into runner evidence.
        pre_docs: Host ``docker inspect`` documents captured before runner start.
        post_docs: Host documents re-inspected by immutable IDs after runner exit.

    Returns:
        Bootstrap SHA-256 for comparison with the runner evidence bundle.

    Raises:
        ValueError: If resources, labels, image IDs, network, or dump mount differ.
    """
    _verify_inspection_set(bootstrap, pre_docs, "pre-run")
    _verify_inspection_set(bootstrap, post_docs, "post-run")
    if _binding(pre_docs) != _binding(post_docs):
        raise ValueError("host restore resources changed during runner session")
    return bootstrap_sha256(bootstrap)


def _verify_inspection_set(
    bootstrap: RestoreBootstrap, value: object, phase: str
) -> None:
    documents = _mapping(value, f"{phase} host inspection")
    _exact_keys(documents, _INSPECTION_KEYS, f"{phase} host inspection")
    _verify_network(bootstrap, documents["network"], phase)
    for name, expected in (
        ("source", bootstrap.source),
        ("scratch", bootstrap.scratch),
        ("runner", bootstrap.runner),
    ):
        _verify_container(
            bootstrap,
            expected,
            documents[name],
            phase,
            needs_dump_mount=name == "runner",
        )


def _verify_network(bootstrap: RestoreBootstrap, value: object, phase: str) -> None:
    document = _mapping(value, f"{phase} network inspection")
    labels = _mapping(document.get("Labels"), f"{phase} network labels")
    if (
        document.get("Name") != bootstrap.network.name
        or document.get("Internal") is not True
        or labels.get(_PROJECT_LABEL) != bootstrap.project_label
    ):
        raise ValueError(f"{phase} host network inspection mismatch")


def _verify_container(
    bootstrap: RestoreBootstrap,
    expected: RestoreServiceBootstrap,
    value: object,
    phase: str,
    *,
    needs_dump_mount: bool,
) -> None:
    document = _mapping(value, f"{phase} {expected.service} inspection")
    config = _mapping(document.get("Config"), f"{phase} {expected.service} config")
    labels = _mapping(config.get("Labels"), f"{phase} {expected.service} labels")
    networks = _mapping(
        _mapping(document.get("NetworkSettings"), "container network settings").get(
            "Networks"
        ),
        f"{phase} {expected.service} networks",
    )
    if (
        document.get("Id") != expected.container_id
        or document.get("Image") != expected.image_digest
        or labels.get(_PROJECT_LABEL) != expected.project_label
        or labels.get(_SERVICE_LABEL) != expected.service
        or set(networks) != {expected.network_name}
    ):
        raise ValueError(f"{phase} host {expected.service} inspection mismatch")
    mounts = document.get("Mounts")
    if not isinstance(mounts, list):
        raise ValueError(f"{phase} host {expected.service} mounts missing")
    dump_mounts = [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and mount.get("Destination") == str(bootstrap.dump_mount.runner_path)
    ]
    if needs_dump_mount:
        if len(dump_mounts) != 1:
            raise ValueError(f"{phase} host runner dump mount mismatch")
        dump = dump_mounts[0]
        if dump.get("Type") != "bind" or dump.get("RW") is not False:
            raise ValueError(f"{phase} host runner dump mount is not read-only")
    elif dump_mounts:
        raise ValueError(f"{phase} database service unexpectedly mounts dump")


def _binding(value: object) -> tuple[object, ...]:
    documents = _mapping(value, "host inspection")
    network = _mapping(documents["network"], "host network inspection")
    bindings: list[object] = [network.get("Name"), network.get("Id")]
    for name in ("source", "scratch", "runner"):
        document = _mapping(documents[name], f"host {name} inspection")
        bindings.extend((document.get("Id"), document.get("Image")))
    return tuple(bindings)


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _exact_keys(value: dict[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(
            f"{name} exact keys mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )
