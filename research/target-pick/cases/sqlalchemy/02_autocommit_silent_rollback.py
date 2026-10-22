# 1.4 autocommits DML on a plain connection; 2.x rolls it back on close. No error either way.
import os, tempfile
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, func, select
db = os.path.join(tempfile.mkdtemp(), "x.db")
e = create_engine(f"sqlite:///{db}")
t = Table("t", MetaData(), Column("id", Integer, primary_key=True))
t.metadata.create_all(e)
with e.connect() as conn:
    conn.execute(t.insert(), [{"id": 1}, {"id": 2}])
with e.connect() as conn:
    print("rows persisted:", conn.execute(select(func.count()).select_from(t)).scalar())
