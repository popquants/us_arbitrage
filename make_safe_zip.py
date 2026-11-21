#!/usr/bin/env python3
"""Create a share-safe workshop ZIP from an explicit allowlist."""
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
OUT=ROOT.parent/'usmart_workshop_share_safe.zip'
FILES=['run.py','make_safe_zip.py','README.md','README_TH.md','FILEMAP.md',
       'requirements.txt','.gitignore','config/workshop.json',
       'config/local/README.txt','data/prices/README.txt',
       'data/sec_13f_zips/README.txt','outputs/README.txt']
FILES += ['config/templates/'+f.name for f in (ROOT/'config'/'templates').iterdir() if f.is_file()]
FILES += ['src/'+f.name for f in (ROOT/'src').iterdir() if f.suffix=='.py']
FILES += ['notebooks/'+f.name for f in (ROOT/'notebooks').iterdir() if f.suffix=='.ipynb']
with zipfile.ZipFile(OUT,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for rel in FILES:
        p=ROOT/rel
        if not p.is_file():raise FileNotFoundError(p)
        z.write(p,arcname='usmart_workshop/'+rel)
with zipfile.ZipFile(OUT) as z:
    assert z.testzip() is None
    names=z.namelist()
    assert not any(name in names for name in (
        'usmart_workshop/config/local/private_key',
        'usmart_workshop/config/local/public_key',
        'usmart_workshop/config/local/api_code'))
    assert not any(n.endswith(('.csv','.parquet','.p12','.pem')) for n in names)
print('Created safe ZIP:',OUT,'files:',len(FILES))
