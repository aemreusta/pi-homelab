"""qBittorrent is stopped for configuration only when a managed setting differs.

Until 2026-10-16 every media deploy stopped and restarted qBittorrent, and with
it every running download, with `changed_when: false` hiding the restart.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "ansible/roles/media/tasks/qbittorrent.yml").read_text())
DEFAULTS = yaml.safe_load((ROOT / "ansible/roles/media/defaults/main.yml").read_text())


def _by_name(name):
    return next(t for t in TASKS if t["name"] == name)


def _all_tasks(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _all_tasks(task.get(key, []))


def test_stop_is_gated_on_a_probed_difference():
    block = _by_name("Configure the stopped qBittorrent profile")
    stop = next(t for t in block["block"] if t["name"] == "Stop qBittorrent before configuration changes")
    assert stop["community.docker.docker_container"]["state"] == "stopped"
    for probe in ("media_qbittorrent_savepath_probe is changed",
                  "media_qbittorrent_settings_probe is changed",
                  "not media_qbittorrent_categories_probe.stat.exists"):
        assert probe in block["when"]
    top_level_stops = [t for t in TASKS if t.get("community.docker.docker_container", {}).get("state") == "stopped"]
    assert top_level_stops == []


def test_probes_are_check_mode_and_match_the_edits():
    settings_probe = _by_name("Probe the managed qBittorrent settings")
    savepath_probe = _by_name("Probe the qBittorrent default save path")
    assert settings_probe["check_mode"] is True and savepath_probe["check_mode"] is True
    block = _by_name("Configure the stopped qBittorrent profile")["block"]
    apply = next(t for t in block if t["name"] == "Configure the managed qBittorrent settings")
    assert apply["ansible.builtin.lineinfile"] == settings_probe["ansible.builtin.lineinfile"]
    assert apply["loop"] == settings_probe["loop"] == "{{ media_qbittorrent_managed_settings }}"
    fix = next(t for t in block if t["name"] == "Fix qBittorrent default save path")
    assert fix["ansible.builtin.replace"] == savepath_probe["ansible.builtin.replace"]


def test_restarts_are_reported_not_hidden():
    for task in _all_tasks(TASKS):
        if "community.docker.docker_container" in task:
            assert "changed_when" not in task, task["name"]


def test_managed_settings_cover_every_section():
    sections = {s["after"] for s in DEFAULTS["media_qbittorrent_managed_settings"]}
    assert sections == {r"^WebUI\\Port=", r"^\[BitTorrent\]", r"^\[Network\]"}
    keys = [s["key"] for s in DEFAULTS["media_qbittorrent_managed_settings"]]
    assert len(keys) == len(set(keys))
    assert "Session\\MaxActiveDownloads" in keys
