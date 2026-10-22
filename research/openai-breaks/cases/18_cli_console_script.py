# Shell usage such as `openai api models.list`: is the console script installed?
import pathlib, subprocess, sys
exe = pathlib.Path(sys.executable).parent / "openai"
if not exe.exists():
    raise FileNotFoundError(f"no `openai` console script next to {sys.executable}")
r = subprocess.run([str(exe), "api", "models.list", "--help"], capture_output=True, text=True)
if r.returncode:
    raise RuntimeError(r.stderr.strip().splitlines()[-1])
print("openai api models.list --help ok")
