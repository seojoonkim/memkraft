import time
import statistics
from memkraft import MemKraft


def bench(base_dir: str, tracked: bool):
    mk = MemKraft(base_dir=base_dir)
    if tracked:
        for i in range(100):
            mk.track(f'ent{i}', entity_type='thing', source='bench')

    write_times = []
    for i in range(100):
        t0 = time.perf_counter()
        mk.update(f'ent{i}', f'info {i}', source='bench')
        write_times.append(time.perf_counter() - t0)

    search_times = []
    for _ in range(100):
        t0 = time.perf_counter()
        try:
            mk.search('info')
        except Exception:
            pass
        search_times.append(time.perf_counter() - t0)

    print('tracked=' + str(tracked))
    print('write_ms p50=%.2f p95=%.2f avg=%.2f total=%.2f' % (
        statistics.median(write_times) * 1000,
        sorted(write_times)[94] * 1000,
        sum(write_times) / len(write_times) * 1000,
        sum(write_times) * 1000,
    ))
    print('search_ms p50=%.2f p95=%.2f avg=%.2f total=%.2f' % (
        statistics.median(search_times) * 1000,
        sorted(search_times)[94] * 1000,
        sum(search_times) / len(search_times) * 1000,
        sum(search_times) * 1000,
    ))


if __name__ == '__main__':
    bench('/tmp/mk-bench-v28-untracked', False)
    bench('/tmp/mk-bench-v28-tracked', True)
