"""minimal_probe.py — PyInstaller 最小探针:排除环境问题。
"""
import sys

def main() -> int:
    print(f"minimal probe OK: python={sys.version.split()[0]} frozen={getattr(sys, 'frozen', False)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
