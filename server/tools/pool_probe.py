import sys
sys.path.insert(0, '/app')
from app import db
names = sorted({l.strip().lower() for l in open('/app/data/ch_pool_names.tsv', encoding='utf-8', errors='ignore') if l.strip()})
print('CH_POOL=%d' % len(names))
rows = db.query_all('SELECT lower(full_name) AS fn FROM gh_repo WHERE lower(full_name) = ANY(%s)', [names])
in_db = {r['fn'] for r in rows}
print('IN_GH_REPO=%d' % len(in_db))
done = db.query_all('SELECT DISTINCT lower(r.full_name) AS fn FROM gh_repo_star_weekly w JOIN gh_repo r ON r.id = w.repo_id')
done_set = {r['fn'] for r in done}
print('ALREADY_COLLECTED=%d' % len(done_set))
todo = sorted(in_db - done_set)
print('TODO=%d' % len(todo))
open('/app/data/todo_names.tsv', 'w').write('\n'.join(todo))
print('MISSING_IN_DB=%d' % len(set(names) - in_db))
