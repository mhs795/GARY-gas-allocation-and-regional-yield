"""Solver backend selection for GARY.

The optimisation is written in Pyomo, so the underlying solver is swappable.
Two backends are supported:

  ``highs`` (default)
      HiGHS via Pyomo's persistent ``appsi_highs`` interface. Multi-threaded and
      fast; this is the backend every published GARY result was produced with.

  ``glpk``
      GLPK via the ``glpsol`` executable. Single-threaded and considerably
      slower on the MILP capacity model, but a pure system package with no
      Python extension module required.

Choose one with the ``GARY_SOLVER`` environment variable or a ``--solver`` flag:

    GARY_SOLVER=glpk ./gas
    python src/solve.py --solver glpk
    python src/main.py --solver glpk

The two solvers describe the same model, so results should agree to within the
MIP gap. They are not bit-identical: where the MILP has ties (degenerate optima),
HiGHS and GLPK can pick different build schedules of equal cost.
"""
import os
import shutil

import pyomo.environ as pyo

DEFAULT_SOLVER = "highs"
SOLVERS = ("highs", "glpk")

# Pyomo solver name for each backend.
_PYOMO_NAME = {
    "highs": "appsi_highs",
    "glpk": "glpk",
}


def normalise(name):
    """Map user input ('HiGHS', 'appsi_highs', 'glpsol') onto a backend key."""
    key = (name or "").strip().lower()
    if key in ("", "default"):
        return DEFAULT_SOLVER
    if key in ("highs", "appsi_highs", "highspy"):
        return "highs"
    if key in ("glpk", "glpsol"):
        return "glpk"
    raise ValueError(f"Unknown solver {name!r}; choose one of {', '.join(SOLVERS)}")


def get_solver_name():
    """The backend currently selected, from GARY_SOLVER (default HiGHS)."""
    return normalise(os.environ.get("GARY_SOLVER", DEFAULT_SOLVER))


def set_solver_name(name):
    """Select a backend for this process *and* any subprocess it spawns.

    The dashboard runs solves in worker processes, so the choice is written back
    into the environment rather than held in a module global.
    """
    key = normalise(name)
    os.environ["GARY_SOLVER"] = key
    return key


def is_available(name=None):
    """True if the selected backend can actually be run on this machine."""
    key = normalise(name) if name else get_solver_name()
    if key == "glpk":
        # Pyomo's glpk plugin shells out to the glpsol binary.
        return shutil.which("glpsol") is not None
    try:
        import highspy  # noqa: F401
        return True
    except ImportError:
        return False


def require_available(name=None):
    """Raise a message that says how to fix it, rather than a Pyomo stack trace."""
    key = normalise(name) if name else get_solver_name()
    if is_available(key):
        return key
    if key == "glpk":
        raise RuntimeError(
            "GLPK selected (GARY_SOLVER=glpk) but the 'glpsol' executable was not "
            "found on PATH. Install it with:  sudo apt install glpk-utils")
    raise RuntimeError(
        "HiGHS selected but the 'highspy' package is not installed. Install it "
        "with:  pip install highspy")


def solver_threads():
    """HiGHS threads per solve, from GARY_SOLVER_THREADS (default 4).

    Sweeps set this to 1 in their worker processes: HiGHS's simplex is serial in
    practice, so several single-threaded solves in parallel beat one solve given
    several threads, and oversubscribing the box makes both slower.
    """
    raw = os.environ.get("GARY_SOLVER_THREADS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 4


def make_solver(rel_gap=None, threads=None, time_limit=None, name=None):
    """Build a configured Pyomo solver for the selected backend.

    ``rel_gap`` is the relative MIP gap; pass None for a pure-LP solve (the
    option is simply not set, which matters for GLPK because glpsol rejects
    MIP-only flags on an LP).

    ``threads`` defaults to :func:`solver_threads` (the GARY_SOLVER_THREADS
    environment variable, else 4).

    A fresh instance is returned every call. The appsi_highs persistent
    interface caches the model between solves, so reusing one across calls
    would carry stale state when variables are fixed or domains changed.
    """
    key = require_available(name)
    opt = pyo.SolverFactory(_PYOMO_NAME[key])

    if key == "highs":
        opt.options['threads'] = solver_threads() if threads is None else threads
        if rel_gap is not None:
            opt.options['mip_rel_gap'] = rel_gap
        if time_limit is not None:
            opt.options['time_limit'] = time_limit
    else:
        # glpsol is single-threaded: there is no threads option to set, and
        # passing one makes it exit with an unrecognised-argument error.
        if rel_gap is not None:
            opt.options['mipgap'] = rel_gap
        if time_limit is not None:
            opt.options['tmlim'] = int(time_limit)

    return opt


def env_time_limit():
    """Optional per-solve wall-clock cap, in seconds, from GARY_SOLVER_TIMELIMIT.

    Mostly useful with GLPK, where a branch-and-bound run on the full-horizon
    capacity model can take far longer than the HiGHS equivalent.
    """
    raw = os.environ.get("GARY_SOLVER_TIMELIMIT", "").strip()
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    return val if val > 0 else None


def describe(name=None):
    """One-line human-readable summary of the active backend."""
    key = normalise(name) if name else get_solver_name()
    label = {"highs": "HiGHS (appsi_highs)", "glpk": "GLPK (glpsol)"}[key]
    if not is_available(key):
        return f"{label} - NOT AVAILABLE"
    tl = env_time_limit()
    return f"{label}{f', time limit {tl:g}s' if tl else ''}"


def add_solver_argument(parser):
    """Attach a --solver flag to an argparse parser."""
    parser.add_argument(
        "--solver", choices=list(SOLVERS), default=None,
        help=f"Optimisation backend (default: $GARY_SOLVER or {DEFAULT_SOLVER})")
    return parser
