"""Run cases/<lib>/*.py in the old and new venv; a break is an error or a different result.

    python run_cases.py > results/matrix.txt
"""
import os, pathlib, subprocess

HERE = pathlib.Path(__file__).parent
PAIRS = {"pydantic": ("pydantic-1", "pydantic-2"), "sqlalchemy": ("sqlalchemy-1", "sqlalchemy-2"),
         "numpy": ("numpy-1", "numpy-2"), "pandas": ("pandas-2", "pandas-3"),
         "langchain": ("langchain-0", "langchain-1"), "llamaindex": ("llamaindex-0", "llamaindex-1"),
         "airflow": ("airflow-2", "airflow-3"), "marshmallow": ("marshmallow-3", "marshmallow-4")}


def run(venv, case):
    env = dict(os.environ, AIRFLOW_HOME=str(HERE / ".venvs" / f"airflow-home-{venv[-1]}"), PYTHONWARNINGS="ignore")
    r = subprocess.run([str(HERE / ".venvs" / venv / "bin" / "python"), "-I", str(case)],
                       capture_output=True, text=True, timeout=300, env=env)
    if r.returncode:
        return False, (r.stderr.strip().splitlines() or ["?"])[-1][:150]
    return True, (r.stdout.strip().splitlines() or [""])[-1][:150]


for lib, (old, new) in PAIRS.items():
    print(f"# {lib}: {old} -> {new}")
    for case in sorted((HERE / "cases" / lib).glob("*.py")):
        ok_o, out_o = run(old, case)
        ok_n, out_n = run(new, case)
        verdict = ("BREAKS (error)" if ok_o and not ok_n else "BREAKS (silently different)" if ok_o and out_o != out_n
                   else "same" if ok_o else "old version fails too")
        print(f"  {case.stem:38} {verdict}\n      old: {'OK ' if ok_o else 'ERR'} {out_o}\n      new: {'OK ' if ok_n else 'ERR'} {out_n}")
