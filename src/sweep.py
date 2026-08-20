"""Run independent GARY scenarios across several processes.

A sweep (the batch button, the reservation sweep) is a set of scenarios that
share nothing: each one loads its own data, solves its own capacity model and
dispatches its own 26 years. That makes the scenario the natural unit of
parallelism — no model changes, no shared state, one solve per worker.

Threads inside the solver are *not* the lever. HiGHS's simplex is serial in
practice, and the duals GARY needs for nodal prices come from simplex, so giving
one dispatch solve four threads measures no faster than giving it one. Workers
therefore pin ``GARY_SOLVER_THREADS=1`` and win by running several solves at
once instead.

Workers are spawned rather than forked: this is called from inside a Dash
background-callback process that already holds diskcache/sqlite handles, and
forking those into children is a good way to find rare, ugly bugs. Spawn costs a
couple of seconds of re-import per worker against solves that run for minutes.

The price of spawn is that every worker re-imports the caller's ``__main__``, so
**any script that calls this must guard its entry point** with
``if __name__ == '__main__':`` — without it the children re-run the sweep on
import and the pool dies during bootstrap. dashboard.py and solve.py are both
guarded; a scratch script is easy to forget.
"""
import os
import time
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed


# Each worker peaks around 0.5 GB, so memory is rarely the binding constraint;
# cores are. Hyperthreads don't help a compute-bound simplex, so the default is
# half the logical CPUs — the physical core count on any normal machine.
def default_workers():
    """Worker count: GARY_WORKERS if set, else half the logical CPUs (min 1)."""
    raw = os.environ.get('GARY_WORKERS', '').strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    try:
        logical = len(os.sched_getaffinity(0))
    except AttributeError:
        logical = os.cpu_count() or 1
    return max(1, logical // 2)


def solve_one(job):
    """Solve one scenario in a worker. ``job`` is ``(key, title, kwargs)``."""
    key, title, kwargs = job
    os.environ['GARY_SOLVER_THREADS'] = '1'
    from solve import solve_scenario
    t0 = time.time()
    results = solve_scenario(**kwargs, log=False)
    return key, results, time.time() - t0


def run_jobs(jobs, workers=None, task=solve_one, on_done=None, on_year=None):
    """Run ``jobs`` and hand each finished one to ``on_done(i, n, key, results, secs)``.

    ``i`` counts completions, not position in ``jobs``: with more than one worker
    scenarios finish out of order, so callers must key off the returned ``key``
    rather than assume the input order.

    With ``workers=1`` the jobs run in this process, which keeps the per-year
    terminal log and the per-year ``on_year(i, n, year, frac)`` progress of a
    sequential run. Above that, workers are silent, year-level progress is not
    available across processes, and one line per finished scenario is printed
    here instead.
    """
    jobs = list(jobs)
    if not jobs:
        return
    workers = default_workers() if workers is None else max(1, int(workers))
    workers = min(workers, len(jobs))

    if workers == 1:
        from solve import solve_scenario
        for i, job in enumerate(jobs, 1):
            key, title, kwargs = job
            t0 = time.time()
            if task is solve_one:
                # In-process, so this branch can do what a worker cannot: log its
                # years and report per-year progress.
                cb = (lambda yr, frac, _i=i: on_year(_i, len(jobs), yr, frac)) if on_year else None
                results = solve_scenario(**kwargs, title=title, callback=cb)
            else:
                key, results, _ = task(job)
            if on_done:
                on_done(i, len(jobs), key, results, time.time() - t0)
        return

    print(f"\nSolving {len(jobs)} scenarios on {workers} workers "
          f"(1 solver thread each)", flush=True)
    titles = {key: title for key, title, _ in jobs}
    # Workers are reused across jobs rather than respawned per job: a spawned
    # worker re-imports the caller's module tree (for the dashboard that means
    # Dash and Plotly, a few seconds and a couple of hundred MB), which is worth
    # paying once per worker but not once per scenario.
    ctx = mp.get_context('spawn')
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = [pool.submit(task, job) for job in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            key, results, secs = fut.result()
            print(f"[{i}/{len(jobs)}]  ✓  {titles.get(key, key)}  ({secs / 60:.1f} min)",
                  flush=True)
            if on_done:
                on_done(i, len(jobs), key, results, secs)
