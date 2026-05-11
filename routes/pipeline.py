"""Pipeline scrape routes."""

import os
import subprocess
import sys
import threading

from flask import Blueprint, current_app, jsonify, request

pipeline_bp = Blueprint("pipeline", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@pipeline_bp.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    status = current_app.config["PIPELINE_STATUS"]
    lock: threading.Lock = current_app.config["PIPELINE_LOCK"]

    with lock:
        if status["running"]:
            return jsonify({"ok": False, "error": "El pipeline ya está corriendo"})

        data = request.get_json() or {}
        query = (data.get("query") or "").strip()
        max_results = int(data.get("max") or 30)
        if not query:
            return jsonify({"ok": False, "error": "Escribí qué negocios buscar"})

        status.update({"running": True, "log": [], "error": None})

    def _run():
        try:
            cmd = [
                sys.executable, "main.py", "run-all",
                "--query", query, "--max", str(max_results),
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.path.dirname(os.path.abspath(__file__ + "/..")),
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    with lock:
                        status["log"].append(line)
            proc.wait()
            if proc.returncode != 0:
                with lock:
                    status["error"] = f"El pipeline terminó con código {proc.returncode}"
        except Exception as e:
            with lock:
                status["error"] = str(e)
        finally:
            with lock:
                status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@pipeline_bp.route("/api/pipeline-status")
def api_pipeline_status():
    return jsonify(current_app.config["PIPELINE_STATUS"])
