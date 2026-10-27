"""Copy root core modules and frontend into backend (AppSail build path is backend/)."""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "backend", "core")
for name in ("features.py", "controller.py", "battery.py"):
    src = os.path.join(ROOT, name)
    dst = os.path.join(CORE, name)
    shutil.copy2(src, dst)
    print("copied", name, "->", dst)

FRONT_SRC = os.path.join(ROOT, "frontend")
FRONT_DST = os.path.join(ROOT, "backend", "frontend")
if os.path.isdir(FRONT_SRC):
    if os.path.isdir(FRONT_DST):
        shutil.rmtree(FRONT_DST)
    shutil.copytree(FRONT_SRC, FRONT_DST)
    print("copied frontend ->", FRONT_DST)
