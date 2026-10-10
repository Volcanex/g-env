"""Locate the operator's instance (gp-core, or legacy gcore) beside the g-env checkout.

bin/g-instance is the shell twin: keep the order identical. Tools in other repos
copy these functions rather than import across repos.
"""
import json
import os
from pathlib import Path

HINT = ('g-instance: no instance found; set G_INSTANCE or create gp-core beside g-env '
        'from g-core/examples. Using framework defaults.')
FILES = {'env': ('GCORE_ENV', '.env'), 'manifest': ('GCORE_MANIFEST', 'manifest.json')}


def framework(environ=os.environ):
    """The g-env checkout: G_ENV, GENV, GENV_DIR, else this file's repo."""
    value = next((environ[v] for v in ('G_ENV', 'GENV', 'GENV_DIR') if environ.get(v)), None)
    return Path(value).resolve() if value else Path(__file__).resolve().parents[1]


def instance(kind='dir', environ=os.environ, repo=None):
    """The instance dir, or its 'env' (passport) or 'manifest' path; None when there is none."""
    variable, name = FILES.get(kind, (None, None))
    if variable and environ.get(variable):
        return Path(environ[variable])
    found = next((Path(environ[v]) for v in ('GCORE_DIR', 'GSITE_GCORE_DIR', 'G_INSTANCE') if environ.get(v)), None)
    if found is None:
        parent = Path(repo or framework(environ)).resolve().parent
        found = next((parent / n for n in ('gp-core', 'gcore') if (parent / n).is_dir()), None)
    return found / name if found and name else found


def service_url(service_id, environ=os.environ):
    """https:// and the domain of a service in the instance manifest; None when it has none."""
    try:
        services = json.loads(instance('manifest', environ).read_text())['services']
        domain = next(s.get('domain') for s in services if s.get('id') == service_id)
    except (AttributeError, KeyError, OSError, StopIteration, TypeError, ValueError):
        return None
    return f'https://{domain}' if domain else None
