import cProfile
import pstats
import io
import logging
import os

from memkraft import MemKraft


logging.disable(logging.CRITICAL)
os.environ.setdefault('MEMKRAFT_QUIET', '1')


def run():
    mk = MemKraft(base_dir='/tmp/mk-prof-v28')
    for i in range(100):
        name = f'ent{i}'
        mk.track(name, entity_type='thing', source='bench')
        mk.update(name, f'info {i}', source='bench')

    pr = cProfile.Profile()
    pr.enable()
    for i in range(500):
        mk.update(f'ent{i % 100}', f'info {i} extra payload ' * 8, source='bench')

    queries = ['info', 'ent5', 'data', 'memory', 'agent']
    for _ in range(20):
        for q in queries:
            try:
                mk.search(q)
            except Exception:
                pass
    pr.disable()

    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats('cumtime').print_stats(60)
    print(s.getvalue())


if __name__ == '__main__':
    run()
