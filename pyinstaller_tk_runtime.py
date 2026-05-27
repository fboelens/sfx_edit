import os
import sys
from pathlib import Path


base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
os.environ.setdefault("TCL_LIBRARY", str(base / "_tcl_data"))
os.environ.setdefault("TK_LIBRARY", str(base / "_tk_data"))
