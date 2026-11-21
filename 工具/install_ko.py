import os, sys, shutil, tarfile, sqlite3, filecmp

kpkg, stage, kindle = sys.argv[1], sys.argv[2], sys.argv[3]

# 1. extract package into an isolated staging dir
shutil.rmtree(stage, ignore_errors=True)
os.makedirs(stage)
with tarfile.open(kpkg, 'r:gz') as t:
    for m in t.getmembers():
        assert not m.name.startswith('/') and '..' not in m.name.split('/'), m.name
    t.extractall(stage, filter='data')
print('解包完成:', sorted(os.listdir(stage)))

# 2. koreader/ -> Kindle root
dst_ko = os.path.join(kindle, 'koreader')
assert not os.path.exists(dst_ko), 'koreader 文件夹已存在'
shutil.copytree(os.path.join(stage, 'koreader'), dst_ko)

# 3. package dir (everything except koreader/) -> kmc/kpm/packages/koreader
pkg = os.path.join(kindle, 'kmc', 'kpm', 'packages', 'koreader')
assert not os.path.exists(pkg)
os.makedirs(pkg)
for x in os.listdir(stage):
    if x == 'koreader':
        continue
    s = os.path.join(stage, x)
    (shutil.copytree if os.path.isdir(s) else shutil.copy2)(s, os.path.join(pkg, x))

# 4. scriptlet -> documents
shutil.copy2(os.path.join(stage, 'scriptlets', 'KOReader.sh'), os.path.join(kindle, 'documents', 'KOReader.sh'))

# 5. remove the half-downloaded package
partial = os.path.join(kindle, 'kmc', 'kpm', 'packages', 'tmp', os.path.basename(kpkg))
if os.path.exists(partial):
    os.remove(partial)

# 6. register in KPM db (backup first)
db = os.path.join(kindle, 'kmc', 'kpm', 'kpm.db')
shutil.copy2(db, os.path.join(os.path.dirname(stage), 'kpm.db.bak'))
c = sqlite3.connect(db)
desc = c.execute("select description from packages where repository='kindlemodding' and id='koreader'").fetchone()[0]
c.execute("insert into installed_packages values (?,?,?,?,?,?,?,?,?)",
          ('koreader', 'kindlemodding', 'KOReader', 'KOReader Team', desc, 2026, 7, 2, 0))
c.commit(); c.close()

# 7. verify
cmp = filecmp.dircmp(os.path.join(stage, 'koreader'), dst_ko)
def walk_diff(d):
    bad = d.left_only + d.right_only + d.diff_files + d.funny_files
    for sub in d.subdirs.values():
        bad += walk_diff(sub)
    return bad
n_src = sum(len(f) for _, _, f in os.walk(os.path.join(stage, 'koreader')))
n_dst = sum(len(f) for _, _, f in os.walk(dst_ko))
print(f'koreader 文件: 源 {n_src} / Kindle {n_dst}, 差异 {len(walk_diff(cmp))}')
print('launch.sh 存在:', os.path.isfile(os.path.join(pkg, 'launch.sh')))
print('KOReader.sh 存在:', os.path.isfile(os.path.join(kindle, 'documents', 'KOReader.sh')))
c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
print('KPM 已安装:', [r[0] for r in c.execute('select id from installed_packages')])
