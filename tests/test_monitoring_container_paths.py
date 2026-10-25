"""Render the actual Compose contracts for custom Docker roots and local images."""

from pathlib import Path

import jinja2
import yaml

ROLES = Path(__file__).resolve().parents[1] / "ansible/roles"


def render(role, **variables):
    folder = ROLES / role
    defaults = yaml.safe_load((folder / "defaults/main.yml").read_text())
    defaults.update(variables)
    return yaml.safe_load(jinja2.Template((folder / "templates/docker-compose.yml.j2").read_text()).render(**defaults))


def test_cadvisor_sees_containerd_spec_root_at_its_absolute_host_path():
    doc = render("cadvisor", cadvisor_docker_data_root="/custom/docker")
    assert "/custom/docker:/custom/docker:ro,rslave" in doc["services"]["cadvisor"]["volumes"]


def test_watchtower_excludes_locally_built_images_without_disabling_other_updates():
    doc = render("watchtower")
    env = doc["services"]["watchtower"]["environment"]
    excluded = next((line.partition("=")[2] for line in env if line.startswith("WATCHTOWER_DISABLE_CONTAINERS=")), "")
    assert set(excluded.split(",")) == {"mlflow"}
    assert "WATCHTOWER_LABEL_ENABLE=true" not in env


def test_expensive_rootfs_walks_are_disabled_but_block_io_is_retained():
    service = render("cadvisor", cadvisor_docker_data_root="/srv/docker")["services"]["cadvisor"]
    option = next((arg for arg in service["command"] if arg.startswith("--disable_metrics=")), "")
    disabled = set(option.partition("=")[2].split(","))
    assert "disk" in disabled
    assert "diskIO" not in disabled
    assert not disabled & {"cpu", "memory", "network", "pressure", "oom_event"}


def test_query_cache_leaves_room_for_aligned_five_minute_alert_samples():
    service = render("victoriametrics")["services"]["victoriametrics"]
    assert "--search.cacheTimestampOffset=10m" in service["command"]
