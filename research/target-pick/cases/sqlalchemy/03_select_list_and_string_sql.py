from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select
e = create_engine("sqlite://")
t = Table("t", MetaData(), Column("id", Integer, primary_key=True))
t.metadata.create_all(e)
with e.connect() as conn:
    print("select([t]) ->", conn.execute(select([t.c.id])).fetchall())
    print("raw string ->", conn.execute("select 1").scalar())
