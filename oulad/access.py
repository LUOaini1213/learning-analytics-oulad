"""Role-based views over gold, and the small-cell suppression rule in plain Python.

The SQL in sql/access is the implementation; `suppress_breakdown` is an independent reference used by the
tests to check it, and by the dashboard export.
"""
from __future__ import annotations

from .common import SQL
from .lineage import strip_comments

ROLES = ("lecturer", "management", "data_engineer")
THRESHOLD = 5


def install(con, app_user: str, app_role: str) -> None:
    """Create the access views for one session. `app_role` must be one of ROLES."""
    if app_role not in ROLES:
        raise ValueError(f"unknown role {app_role!r}")
    con.execute("create schema if not exists access")
    con.execute("create schema if not exists run")
    con.execute("create or replace table run.session (app_user varchar, app_role varchar)")
    con.execute("insert into run.session values (?, ?)", [app_user, app_role])
    for path in sorted((SQL / "access").glob("*.sql")):
        for statement in strip_comments(path.read_text(encoding="utf-8")).split(";"):
            if statement.strip():
                con.execute(statement)


def suppress_breakdown(counts: dict[str, int], threshold: int = THRESHOLD) -> dict[str, int | None]:
    """Withhold small cells in one breakdown (the cells add up to a published total).

    Primary: a count below `threshold` is withheld. Complementary: if exactly one cell is withheld, the
    smallest remaining cell (ties broken by label) is withheld as well, otherwise total minus the published
    cells would give the hidden value back.
    """
    hidden = {k for k, v in counts.items() if v < threshold}
    if len(hidden) == 1:
        rest = sorted((v, k) for k, v in counts.items() if k not in hidden)
        if rest:
            hidden.add(rest[0][1])
    return {k: (None if k in hidden else v) for k, v in counts.items()}


def recoverable_by_differencing(published: dict[str, int | None], total: int) -> bool:
    """True if a reader who knows the total can work out a withheld cell exactly."""
    return sum(1 for v in published.values() if v is None) == 1 and total is not None


def suppress_rate(n: int, k: int, threshold: int = THRESHOLD) -> int | None:
    """A numerator k out of n is shown only if k and n - k are both at least `threshold`."""
    return k if (n >= threshold and k >= threshold and n - k >= threshold) else None
