"""One-off: rewrite an existing precalculated_results.pkl in the compressed
column format.

results_io.load already reads the old record-list pickles, so this is just a
read-and-rewrite -- no re-solving. Loading the old file is the memory-hungry
part (the record lists cost several GB expanded); the frames replace them as the
walk proceeds, so usage falls away again before anything is written.

    python src/migrate_results.py [path]

Writes <path>.new, checks it reads back, then swaps it in and keeps the original
as <path>.bak.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import results_io


def main():
    path = (sys.argv[1] if len(sys.argv) > 1 else
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'data', 'precalculated_results.pkl'))
    before = os.path.getsize(path)
    print(f'reading {path} ({before/1e6:.0f} MB)...', flush=True)
    data = results_io.load(path)
    scenarios = data.get('all_scenarios', {})
    n = sum(len(v or []) for v in scenarios.values())
    print(f'  {len(scenarios)} scenarios, {n} year-blocks', flush=True)

    tmp = path + '.new'
    results_io.save(data, tmp)
    after = os.path.getsize(tmp)
    del data, scenarios

    # Verify it reads back before touching the original.
    check = results_io.load(tmp)
    assert sum(len(v or []) for v in check['all_scenarios'].values()) == n, \
        'year-block count changed in the rewrite'
    del check

    os.replace(path, path + '.bak')
    os.replace(tmp, path)
    print(f'\n{before/1e6:.0f} MB -> {after/1e6:.1f} MB  ({before/after:.0f}x smaller)',
          flush=True)
    print(f'original kept at {path}.bak -- delete it once you are happy.', flush=True)


if __name__ == '__main__':
    main()
