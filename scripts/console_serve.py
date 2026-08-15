"""Run the repository-native Operator Console.

Usage: KAWA_DSN=dbname=kawa python scripts/console_serve.py [port] [host]

host defaults to 127.0.0.1; pass 0.0.0.0 to serve the tailnet (the systemd
unit kawa-console.service does this so http://<node>:8099/ works fleet-wide).
"""
import sys

from kawa.console.server import serve

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    serve(host=host, port=port)
