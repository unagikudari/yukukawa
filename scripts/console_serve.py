"""Run the repository-native Operator Console. Usage: KAWA_DSN=dbname=kawa python scripts/console_serve.py [port]"""
import sys

from kawa.console.server import serve

if __name__ == "__main__":
    serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8099)
