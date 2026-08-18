"""Local-only HTTP host shared by the designer and visual reader."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from appendix.xvlink_core import (
    build_integration_cells,
    cells_to_preview,
    connection_suggestions,
    export_project,
    load_project,
    load_workbook_model,
    new_project,
    save_project,
    validate_project,
)


STATIC_ROOT = Path(__file__).resolve().parent / "static"


def native_dialog(mode: str, kind: str) -> str:
    """Use the OS file dialog while keeping the browser fully offline."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if mode == "save":
            extension = ".xvlink.json" if kind == "project" else ".xlsx"
            types = [("XVLink project", "*.xvlink.json")] if kind == "project" else [("Excel workbook", "*.xlsx")]
            return filedialog.asksaveasfilename(defaultextension=extension, filetypes=types)
        types = [("XVLink project", "*.xvlink.json")] if kind == "project" else [("Excel workbook", "*.xlsx")]
        return filedialog.askopenfilename(filetypes=types)
    finally:
        root.destroy()


class AppHandler(BaseHTTPRequestHandler):
    mode = "designer"

    def log_message(self, format: str, *args: object) -> None:
        # Do not leak workbook contents or query strings into logs.
        print(f"[{self.log_date_time_string()}] {self.command} {urlparse(self.path).path}")

    def _json(self, value: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 20 * 1024 * 1024:
            raise ValueError("请求体超过 20 MiB 限制")
        return json.loads(self.rfile.read(length) or b"{}")

    def _error(self, exc: Exception) -> None:
        self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/workbook":
                self._json({"ok": True, "model": load_workbook_model(query.get("path", [""])[0])})
                return
            if parsed.path == "/api/dialog":
                self._json({"ok": True, "path": native_dialog(query.get("mode", ["open"])[0], query.get("kind", ["xlsx"])[0])})
                return
            if parsed.path == "/api/project/load":
                self._json({"ok": True, "project": load_project(query.get("path", [""])[0])})
                return
            self._static(parsed.path)
        except Exception as exc:  # user-facing boundary
            self._error(exc)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._body()
            if parsed.path == "/api/project/new":
                model = body["model"]
                self._json({"ok": True, "project": new_project(model["source"], model["fingerprint"], body["roles"])})
            elif parsed.path == "/api/suggestions":
                self._json({"ok": True, "suggestions": connection_suggestions(body["model"], body["roles"])})
            elif parsed.path == "/api/validate":
                self._json({"ok": True, "diagnostics": validate_project(body["model"], body["project"])})
            elif parsed.path == "/api/preview":
                self._json({"ok": True, "preview": cells_to_preview(build_integration_cells(body["model"], body["project"]))})
            elif parsed.path == "/api/export":
                self._json({"ok": True, "result": export_project(body["model"], body["project"], body["output"], overwrite=bool(body.get("overwrite")))})
            elif parsed.path == "/api/project/save":
                self._json({"ok": True, "path": str(save_project(body["path"], body["project"]))})
            else:
                self._json({"ok": False, "error": "API 不存在"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # user-facing boundary
            self._error(exc)

    def _static(self, request_path: str) -> None:
        page = "designer.html" if self.mode == "designer" else "viewer.html"
        relative = request_path.lstrip("/") or page
        if relative == "index.html":
            relative = page
        candidate = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT not in candidate.parents or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def serve(mode: str, host: str, port: int, open_browser: bool) -> None:
    handler = type("SelectedAppHandler", (AppHandler,), {"mode": mode})
    server = ThreadingHTTPServer((host, port), handler)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"XVLink {mode} 已启动：{url}")
    print("按 Ctrl+C 停止。服务仅绑定本机，不使用互联网。")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="XVLink 离线可视化工具")
    parser.add_argument("--mode", choices=["designer", "viewer"], default="designer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 表示自动选择空闲端口")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("为保护本地工作簿，只允许绑定回环地址")
    serve(args.mode, args.host, args.port, not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
