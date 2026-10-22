from sqlalchemy import create_engine
e = create_engine("sqlite://")
print("engine.execute ->", e.execute("select 1").scalar())
