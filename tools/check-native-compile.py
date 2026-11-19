"""Check the port against the configured Android headers without writing Ninja outputs."""
from pathlib import Path
import shlex
import subprocess
import sys

build = Path(sys.argv[1]).resolve()
target = "plugins/tools/basictools/CMakeFiles/kritadefaulttools_static.dir/kis_tool_prokrita.cpp.o"
commands = subprocess.check_output(["ninja", "-C", str(build), "-t", "commands", target], text=True)
command = next(line for line in reversed(commands.splitlines()) if " -c " in line and "kis_tool_prokrita.cpp" in line)
args = shlex.split(command)
filtered = []
i = 0
while i < len(args):
    if args[i] in ("-o", "-MF", "-MT", "-MQ"):
        i += 2
    elif args[i] in ("-MD", "-MMD", "-c"):
        i += 1
    else:
        filtered.append(args[i])
        i += 1
result = subprocess.run(filtered + ["-fsyntax-only"], cwd=build)
if result.returncode:
    sys.exit(result.returncode)
print("Native tool Android compiler check passed")
