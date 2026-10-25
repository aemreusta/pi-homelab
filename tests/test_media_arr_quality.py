"""Radarr/Sonarr size ceilings: render the role's Jinja against API-shaped data.

The tasks diff live quality definitions/profiles and PUT only what differs. A
wrong expression here either rewrites every definition on each deploy or never
applies the ceiling that keeps 20-40 GB disc copies off a direct-play Pi.
"""

import json
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "ansible/roles/media/tasks/configure-arr-quality.yml").read_text())
DEFAULTS = yaml.safe_load((ROOT / "ansible/roles/media/defaults/main.yml").read_text())


def _env():
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)  # noqa: S701 - renders JSON, not HTML
    env.filters["to_json"] = json.dumps
    env.filters["from_json"] = json.loads
    return env


def _task(name):
    return next(t for t in TASKS if t["name"] == name)


def _target(name):
    target = dict(next(t for t in DEFAULTS["media_arr_quality_targets"] if t["name"] == name))
    return target


def _definition(id_, name, min_, pref, max_):
    # Radarr omits preferredSize/maxSize (rather than sending null) when unbounded.
    sizes = {k: v for k, v in (("preferredSize", pref), ("maxSize", max_)) if v is not None}
    return {"id": id_, "quality": {"id": id_, "name": name}, "title": name, "minSize": min_, **sizes}


def _size_updates(target, definitions):
    expr = _task("Apply arr release size ceilings")["vars"]["media_arr_size_updates"]
    item = {"item": target, "json": definitions}
    return json.loads(_env().from_string(expr).render(item=item))


def test_unbounded_bluray_and_web_definitions_get_ceilings():
    target = _target("radarr")
    live = [_definition(7, "Bluray-1080p", 0, None, None), _definition(3, "WEBDL-1080p", 0, 95, 100),
            _definition(30, "Remux-1080p", 0, None, None), _definition(4, "HDTV-720p", 0, 95, 100)]
    updates = {u["quality"]["name"]: u for u in _size_updates(target, live)}
    assert set(updates) == {"Bluray-1080p", "WEBDL-1080p"}
    for name, update in updates.items():
        want = target["sizes"][name]
        assert (update["minSize"], update["preferredSize"], update["maxSize"]) == (
            want["min"], want["preferred"], want["max"])
        assert update["id"] == next(d["id"] for d in live if d["quality"]["name"] == name)


def test_matching_definitions_submit_nothing():
    target = _target("sonarr")
    live = [_definition(i, name, s["min"], s["preferred"], s["max"])
            for i, (name, s) in enumerate(target["sizes"].items(), start=1)]
    assert _size_updates(target, live) == []


def _profile_items(target, profile):
    expr = _task("Disallow disc-copy qualities in the in-use profile")["vars"]["media_arr_profile_items"]
    item = {"item": target, "json": [profile]}
    return json.loads(_env().from_string(expr).render(item=item, media_arr_profile=profile))


def test_remux_is_disallowed_and_groups_are_preserved():
    target = _target("radarr")
    group = {"name": "WEB 1080p", "allowed": True, "id": 1001,
             "items": [{"quality": {"id": 3, "name": "WEBDL-1080p"}, "items": [], "allowed": True}]}
    profile = {"id": 4, "name": "HD-1080p", "cutoff": 7, "items": [
        {"quality": {"id": 7, "name": "Bluray-1080p"}, "items": [], "allowed": True},
        {"quality": {"id": 30, "name": "Remux-1080p"}, "items": [], "allowed": True},
        group,
    ]}
    items = _profile_items(target, profile)
    allowed = {i["quality"]["name"]: i["allowed"] for i in items if "quality" in i}
    assert allowed == {"Bluray-1080p": True, "Remux-1080p": False}
    assert items[2] == group
    assert _profile_items(target, {**profile, "items": items}) == items


def test_new_grabs_go_to_the_top_of_the_qbittorrent_queue():
    expr = _task("Put new arr grabs at the top of the qBittorrent queue")["vars"]["media_arr_client_fields"]
    for name in ("radarr", "sonarr"):
        target = _target(name)
        recent, older = target["priority_fields"]
        assert recent.startswith("recent") and older.startswith("older")
        fields = [{"name": "host", "value": "qbittorrent"},
                  {"name": recent, "value": 0}, {"name": older, "value": 0},
                  {"name": "initialState", "value": 0}]
        client = {"id": 1, "implementation": "QBittorrent", "fields": fields}
        rendered = json.loads(_env().from_string(expr).render(item=[{"item": target}, client]))
        assert {f["name"]: f["value"] for f in rendered} == {
            "host": "qbittorrent", recent: 1, older: 1, "initialState": 0}
        again = json.loads(_env().from_string(expr).render(item=[{"item": target}, {**client, "fields": rendered}]))
        assert again == rendered


def test_live_api_phase_is_check_mode_gated():
    main = yaml.safe_load((ROOT / "ansible/roles/media/tasks/main.yml").read_text())
    include = next(t for t in main if isinstance(t.get("ansible.builtin.include_tasks"), dict)
                   and t["ansible.builtin.include_tasks"]["file"] == "configure-arr-quality.yml")
    assert "not ansible_check_mode" in include["when"]
