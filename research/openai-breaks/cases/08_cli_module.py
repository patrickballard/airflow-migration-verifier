import subprocess, sys
r = subprocess.run([sys.executable, "-m", "openai", "--help"], capture_output=True, text=True)
if r.returncode: raise RuntimeError((r.stderr.strip().splitlines() or ["?"])[-1])
print("python -m openai --help ok")
