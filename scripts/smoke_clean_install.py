from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="braincell-clean-install-") as directory:
        environment = Path(directory) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process_env = dict(
            os.environ,
            PORT=str(port),
            BRAINCELLGPT_HOST="127.0.0.1",
            BRAINCELLGPT_DATA_DIR=str(Path(directory) / "data"),
        )
        for key in list(process_env):
            if key.endswith("_API_KEY") or key == "CUSTOM_API_KEY":
                process_env.pop(key, None)
        server = subprocess.Popen(
            [str(python), str(ROOT / "app.py")], cwd=ROOT, env=process_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"Backend exited during startup with status {server.returncode}.")
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1) as response:
                        if response.status != 200:
                            raise RuntimeError("Health check returned a non-200 status.")
                        if b'"status": "ok"' not in response.read():
                            raise RuntimeError("Health check did not report readiness.")
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.2)
            else:
                raise RuntimeError("Backend did not become ready within 60 seconds.")

            with urllib.request.urlopen(base + "/", timeout=3) as response:
                if response.status != 200 or b"BraincellGPT" not in response.read():
                    raise RuntimeError("The application page failed its clean-install smoke check.")
            create_request = urllib.request.Request(
                base + "/api/conversations", data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(create_request, timeout=3) as response:
                if response.status != 201:
                    raise RuntimeError("Conversation creation did not return 201.")
                import json
                conversation = json.loads(response.read().decode("utf-8"))
            if conversation.get("messages_used") != 0 or conversation.get("message_limit") != 10:
                raise RuntimeError("New conversation did not start with a 10-message allowance.")
            usage_request = urllib.request.Request(base + "/api/conversations/usage?id=" + conversation["conversation_id"])
            with urllib.request.urlopen(usage_request, timeout=3) as response:
                usage = json.loads(response.read().decode("utf-8"))
            if usage.get("messages_used") != 0:
                raise RuntimeError("Conversation usage did not persist through the HTTP API.")
            request = urllib.request.Request(
                base + "/api/chat", data=b'{"message":"","conversation_id":"' + conversation["conversation_id"].encode("ascii") + b'","request_id":"' + b"a" * 32 + b'"}', headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                urllib.request.urlopen(request, timeout=3)
            except urllib.error.HTTPError as response:
                if response.code != 400:
                    raise RuntimeError(f"Empty chat request returned {response.code}, expected 400.")
            else:
                raise RuntimeError("Empty chat request unexpectedly succeeded.")
            print("Clean virtual-environment startup and HTTP smoke checks passed.")
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
