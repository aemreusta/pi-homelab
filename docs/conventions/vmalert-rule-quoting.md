# Convention: quoting `$value` / `printf` in vmalert rule annotations

**TL;DR — annotations that surface a Prometheus/Go template expression
(`$value`, `$labels`, `printf`) MUST use a single-quoted *outer* YAML scalar
with the inner Go template wrapped in a double-quoted Jinja2 block and
backslash-escaped inner quotes.** Double-quoted outer scalars collide with the
`"` in `printf "%.0f"` after Jinja2 renders, breaking the YAML and crash-looping
vmalert on deploy.

This is enforced in CI by `tests/test_vmalert_rules.py` (renders every
`rules/*.yml.j2` and `yaml.safe_load`s the result, with a pinned regression
fixture for the exact broken shape below). The convention exists so PRs are
*written* correctly, not just caught after the fact.

## Why this is a recurring trap

The `.j2` source looks fine to the eye — the colliding quotes don't exist until
**Jinja2 renders** the escaped Go-template block into literal text. The text
that reaches vmalert (and that the YAML parser must accept) is the *rendered*
form, not the source. So the bug is invisible in review unless you mentally
render the template.

It has shipped twice:

- `368e10a` — a pipeline alert rule file (qbit-cleanup sprint)
- `86d1c25` — a transcode-watcher alert rule file (hotfix)

Both are the **same shape**. See [docs/incidents/](../incidents).

## ✅ Safe pattern (do this)

```yaml
        annotations:
          # Outer YAML scalar: SINGLE quotes.
          # Inner Jinja2 block: DOUBLE quotes, with \" around the printf format.
          summary: 'Queue: {{ "{{ $value | printf \"%.0f\" }}" }} active errors'
```

After Jinja2 renders, this becomes:

```yaml
          summary: 'Queue: {{ $value | printf "%.0f" }} active errors'
```

— a single-quoted YAML scalar containing double quotes, which is valid. vmalert
then resolves `{{ $value | printf "%.0f" }}` itself at alert-evaluation time.

A `$value` with no `printf` is even simpler (single-quoted Jinja inner is fine
because there are no inner double quotes to collide):

```yaml
          summary: 'Swap at {{ '{{ $value | printf "%.0f" }}' }}%'
```

## ❌ Broken anti-pattern (never do this)

```yaml
        annotations:
          # Outer YAML scalar: DOUBLE quotes. After render the printf's
          # inner " collide with the outer " → YAML parse error →
          # vmalert restart loop on deploy.
          summary: "Queue: {{ '{{ $value | printf \"%.0f\" }}' }} active errors"
```

Renders to (invalid — the scalar closes early at `printf "`):

```yaml
          summary: "Queue: {{ $value | printf "%.0f" }} active errors"
```

## Rules of thumb

1. **Surfacing a Go template (`$value`/`$labels`/`printf`)?** → single-quoted
   outer, double-quoted inner Jinja block, `\"` around the format string.
2. **Plain text with an apostrophe** (e.g. `queue's`)? → double-quoted outer is
   fine *as long as there is no rendered inner `"`*.
3. **Never** put a naked `{{ $value }}` in a template — Jinja2 will try to
   resolve `$value` as a Jinja variable and eat it. Always wrap Go templates in
   a Jinja string block (`{{ "...{{ $value }}..." }}`).
4. When in doubt, run `python3 -m pytest tests/test_vmalert_rules.py` — it
   renders and YAML-parses exactly what Ansible will ship.

## Deferred

A Jinja2 macro (`{{ annotation_pct($value) }}`) that expands to the safe form
would eliminate the choice entirely. Deferred until a third incident — two
incidents justify a lint rule + this convention; a third would justify the
macro. (PM review O1.3.)
