import sys
from pathlib import Path

# 让 backend/ 下的模块(memory.py 等)可被测试 import
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
