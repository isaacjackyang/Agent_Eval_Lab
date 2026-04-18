from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = ROOT / "configs" / "experiments"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diagnostics.run_diagnostics import summarize_failure_clusters, summarize_trace_entries
from evolution.mutator import (
    EVOLUTION_MODE_OPTIONS,
    apply_heat_map_overrides,
    heat_map_candidate_count,
    heat_map_dimension_options,
    heat_map_task_type_options,
    resolve_heat_map_plan,
    sampling_parameters_for_provider,
    sampling_provider_for_config,
    selected_sampling_parameters_for_config,
    supports_architecture_evolution,
)
from evolution.heat_map_verifier import estimate_relayer_scan_total_evals, resolve_heat_map_verification_settings
from evolution.relayer_plan import summarize_relayer_config
from generators.task_dispatch import TASK_TYPE_OPTIONS

PROGRESS_KEYS = (
    "task_type",
    "suite_progress_current",
    "suite_progress_target",
    "suite_progress_text",
    "progress_current",
    "progress_target",
    "progress_text",
)

LOCAL_AI_DETECTION_TTL_SEC = 30.0
AI_PROCESS_HINTS = (
    {
        "id": "ollama",
        "label": "Ollama",
        "match": ("ollama",),
        "ports": (11434,),
    },
    {
        "id": "lm_studio",
        "label": "LM Studio",
        "match": ("lm studio", "lmstudio", "lms"),
        "ports": (1234,),
    },
    {
        "id": "llama_cpp",
        "label": "llama.cpp",
        "match": ("llama-server", "llama_cpp", "llama-cpp", "llama.cpp"),
        "ports": (8080, 8000),
    },
    {
        "id": "vllm",
        "label": "vLLM",
        "match": ("vllm",),
        "ports": (8000,),
    },
    {
        "id": "text_generation_webui",
        "label": "Text Generation WebUI",
        "match": ("text-generation-webui", "text_generation_webui"),
        "ports": (7860, 5000, 8000),
    },
    {
        "id": "koboldcpp",
        "label": "KoboldCpp",
        "match": ("koboldcpp",),
        "ports": (5001,),
    },
    {
        "id": "openclaw",
        "label": "OpenClaw",
        "match": ("openclaw",),
        "ports": (),
    },
)
AI_ENDPOINT_PROBES = (
    {
        "hint_id": "ollama",
        "label": "Ollama",
        "base_url": "http://127.0.0.1:11434",
        "path": "/api/tags",
        "parser": "ollama",
        "port": 11434,
    },
    {
        "hint_id": "lm_studio",
        "label": "LM Studio",
        "base_url": "http://127.0.0.1:1234",
        "path": "/v1/models",
        "parser": "openai",
        "port": 1234,
    },
    {
        "hint_id": "llama_cpp",
        "label": "llama.cpp",
        "base_url": "http://127.0.0.1:8080",
        "path": "/v1/models",
        "parser": "openai",
        "port": 8080,
    },
    {
        "hint_id": None,
        "label": "OpenAI-compatible endpoint",
        "base_url": "http://127.0.0.1:8000",
        "path": "/v1/models",
        "parser": "openai",
        "port": 8000,
    },
    {
        "hint_id": None,
        "label": "OpenAI-compatible endpoint",
        "base_url": "http://127.0.0.1:8001",
        "path": "/v1/models",
        "parser": "openai",
        "port": 8001,
    },
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _windows_no_window_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_subprocess(command: list[str], *, timeout: int, check: bool = False) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "check": check,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _windows_no_window_flags()
    return subprocess.run(command, **kwargs)


def _write_pid_file(path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid_file(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    try:
        recorded_pid = path.read_text(encoding="utf-8").strip()
    except Exception:
        recorded_pid = ""
    if recorded_pid and recorded_pid != str(os.getpid()):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


class RunController:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.Lock()
        self.logs_dir = root / "runs" / "control"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.live_status_path = root / "runs" / "live_status.json"
        self.live_stream_path = root / "runs" / "live_stream.jsonl"
        self.process: subprocess.Popen[str] | None = None
        self.log_handle = None
        self.current: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self.local_ai_cache: dict[str, Any] | None = None

    def _config_dir(self) -> Path:
        return CONFIGS_DIR

    def _config_summary(self, path: Path) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "name": path.name,
            "relative_path": self._relative_path(path),
            "config_id": path.stem,
            "runner": None,
            "regression_case_count": None,
            "sampling_provider": None,
            "sampling_parameters": [],
            "selected_sampling_parameters": [],
            "default_evolution_mode": "model_params",
            "architecture_evolution_supported": False,
            "heat_map_dimensions": heat_map_dimension_options(),
            "heat_map_task_types": heat_map_task_type_options(),
            "heat_map_settings": {},
            "heat_map_plan": None,
            "relayer": summarize_relayer_config({}),
        }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return summary
        if isinstance(payload, dict):
            summary["config_id"] = payload.get("config_id") or summary["config_id"]
            summary["runner"] = payload.get("runner")
            regression_path_value = payload.get("regression_suite", {}).get("path")
            if regression_path_value:
                regression_path = (self.root / str(regression_path_value)).resolve()
                try:
                    regression_suite = json.loads(regression_path.read_text(encoding="utf-8"))
                    summary["regression_case_count"] = len(regression_suite.get("cases", []))
                except Exception:
                    summary["regression_case_count"] = None
            provider = sampling_provider_for_config(payload)
            summary["sampling_provider"] = provider
            summary["sampling_parameters"] = sampling_parameters_for_provider(provider)
            summary["selected_sampling_parameters"] = selected_sampling_parameters_for_config(payload)
            summary["default_evolution_mode"] = str(payload.get("nightly", {}).get("evolution_mode", "model_params"))
            summary["architecture_evolution_supported"] = supports_architecture_evolution(payload)
            summary["relayer"] = summarize_relayer_config(payload)
            heat_map_cfg = payload.get("nightly", {}).get("heat_map", {})
            if isinstance(heat_map_cfg, dict):
                scan_cfg = heat_map_cfg.get("scan", {})
                if not isinstance(scan_cfg, dict):
                    scan_cfg = {}
                probe_a_cfg = heat_map_cfg.get("probe_a", {})
                if not isinstance(probe_a_cfg, dict):
                    probe_a_cfg = {}
                probe_b_cfg = heat_map_cfg.get("probe_b", {})
                if not isinstance(probe_b_cfg, dict):
                    probe_b_cfg = {}
                summary["heat_map_settings"] = {
                    "scan": {
                        "start_layer_min": scan_cfg.get("start_layer_min"),
                        "end_layer_max": scan_cfg.get("end_layer_max"),
                        "min_block_len": scan_cfg.get("min_block_len"),
                        "max_block_len": scan_cfg.get("max_block_len"),
                        "repeat_count": scan_cfg.get("repeat_count"),
                    },
                    "probe_a": {
                        "task_type": probe_a_cfg.get("task_type"),
                        "seeds": probe_a_cfg.get("seeds"),
                    },
                    "probe_b": {
                        "task_type": probe_b_cfg.get("task_type"),
                        "seeds": probe_b_cfg.get("seeds"),
                    },
                    "top_k": heat_map_cfg.get("top_k"),
                    "verify": heat_map_cfg.get("verify", {}),
                }
            if summary["architecture_evolution_supported"]:
                try:
                    summary["heat_map_plan"] = resolve_heat_map_plan(payload)
                except Exception:
                    summary["heat_map_plan"] = None
        return summary

    def _default_config_path(self) -> Path | None:
        preferred = [
            self._config_dir() / "local_llama_cpp_agent.json",
            self._config_dir() / "default_mvp.json",
        ]
        for candidate in preferred:
            if candidate.exists():
                return candidate
        matches = sorted(self._config_dir().glob("*.json"))
        return matches[0] if matches else None

    def _with_local_ai(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["local_ai"] = self.list_local_ai()
        return data

    def _reports_dir(self) -> Path:
        default_path = self._default_config_path()
        if default_path is not None:
            try:
                payload = self._load_config_payload(default_path)
            except Exception:
                payload = {}
            reports_dir_value = payload.get("paths", {}).get("reports_dir")
            if reports_dir_value:
                return (self.root / str(reports_dir_value)).resolve()
        return (self.root / "reports").resolve()

    def _load_json_path(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _load_history_payload(self, file_name: str) -> dict[str, Any]:
        return self._load_json_path(self._reports_dir() / file_name) or {"history": []}

    def _load_live_stream_rows(self, limit: int = 160) -> list[dict[str, Any]]:
        if not self.live_stream_path.exists():
            return []
        try:
            lines = [line for line in self.live_stream_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-max(1, limit):]:
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _latest_relayer_scan_snapshot(self) -> dict[str, Any] | None:
        relayer_root = self._reports_dir() / "relayer_scans"
        if not relayer_root.exists():
            return None
        manifests: list[tuple[str, Path, dict[str, Any]]] = []
        for path in relayer_root.glob("*/manifest.json"):
            payload = self._load_json_path(path)
            if not payload:
                continue
            manifests.append((str(payload.get("created_at") or ""), path.parent, payload))
        if not manifests:
            return None
        manifests.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
        _created_at, artifact_dir, manifest = manifests[0]
        summary = self._load_json_path(artifact_dir / "summary.json") or {}
        resume_state = self._load_json_path(artifact_dir / "resume_state.json") or {}
        verification = manifest.get("verification") if isinstance(manifest.get("verification"), dict) else {}
        baseline_decision = manifest.get("baseline_decision") if isinstance(manifest.get("baseline_decision"), dict) else {}
        return {
            "run_id": manifest.get("run_id"),
            "config_id": manifest.get("config_id"),
            "candidate_count": manifest.get("candidate_count"),
            "baseline_score": manifest.get("baseline_score"),
            "output_dir": manifest.get("output_dir"),
            "cells_dir": manifest.get("cells_dir"),
            "resume_state_path": manifest.get("resume_state_path"),
            "resume_phase": resume_state.get("phase"),
            "completed_candidates": resume_state.get("completed_candidates"),
            "pending_candidates": resume_state.get("pending_candidates"),
            "max_workers": manifest.get("max_workers"),
            "reused_cells": manifest.get("reused_cells"),
            "top_candidates": summary.get("top_candidates", [])[:5] if isinstance(summary.get("top_candidates"), list) else [],
            "best_cell": summary.get("best_cell"),
            "verification": verification,
            "baseline_decision": baseline_decision,
        }

    def _lineage_snapshot(self) -> list[dict[str, Any]]:
        history = self._load_history_payload("config_history.json").get("history", [])
        entries = [item for item in history if isinstance(item, dict)]
        entries.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
        return [
            {
                "ts": item.get("ts"),
                "suite_id": item.get("suite_id"),
                "event": item.get("event"),
                "config_id": item.get("config_id"),
                "reference_config_id": item.get("reference_config_id"),
                "mutation_profile": item.get("mutation_profile"),
                "mutation_target": item.get("mutation_target"),
                "fitness": item.get("fitness"),
                "baseline_status": item.get("baseline_status"),
            }
            for item in entries[:10]
        ]

    def _failure_snapshot(self) -> dict[str, Any]:
        payload = self._load_history_payload("failure_clusters.json")
        history = payload.get("history", []) if isinstance(payload, dict) else []
        latest = history[-1] if history else None
        return {
            "latest": latest,
            "clusters": summarize_failure_clusters(payload, limit=6),
        }

    def _trace_snapshot(self) -> dict[str, Any]:
        payload = self._load_history_payload("trace_analysis_history.json")
        live_rows = self._load_live_stream_rows()
        type_counts = Counter(str(item.get("type") or "system") for item in live_rows)
        name_counts = Counter(str(item.get("name") or item.get("type") or "event") for item in live_rows)
        latest_row = live_rows[-1] if live_rows else {}
        return {
            "entries": summarize_trace_entries(payload, limit=6),
            "live": {
                "event_count": len(live_rows),
                "latest_event_id": latest_row.get("event_id"),
                "latest_event_seq": latest_row.get("event_seq"),
                "latest_replay_key": latest_row.get("replay_key"),
                "type_counts": dict(type_counts),
                "top_names": [
                    {"name": name, "count": count}
                    for name, count in name_counts.most_common(5)
                ],
            },
        }

    def _diagnostics_snapshot(self) -> dict[str, Any]:
        return {
            "latest_relayer_scan": self._latest_relayer_scan_snapshot(),
            "lineage": self._lineage_snapshot(),
            "failure": self._failure_snapshot(),
            "trace": self._trace_snapshot(),
        }

    def _list_processes(self) -> list[dict[str, Any]]:
        if os.name == "nt":
            command = [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                (
                    "$ErrorActionPreference='Stop'; "
                    "Get-CimInstance Win32_Process | "
                    "Select-Object ProcessId, Name, CommandLine | "
                    "ConvertTo-Json -Compress"
                ),
            ]
            try:
                result = _run_subprocess(command, timeout=8)
            except Exception:
                return []
            if result.returncode != 0 or not result.stdout.strip():
                return []
            try:
                payload = json.loads(result.stdout)
            except Exception:
                return []
            rows = payload if isinstance(payload, list) else [payload]
            processes: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                processes.append(
                    {
                        "pid": row.get("ProcessId"),
                        "name": row.get("Name"),
                        "command_line": row.get("CommandLine") or "",
                    }
                )
            return processes

        try:
            result = _run_subprocess(["ps", "-axo", "pid=,comm=,args="], timeout=8)
        except Exception:
            return []
        if result.returncode != 0:
            return []
        processes = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            pid, name = parts[0], parts[1]
            command_line = parts[2] if len(parts) > 2 else ""
            processes.append({"pid": pid, "name": name, "command_line": command_line})
        return processes

    def _fetch_json(self, url: str) -> Any | None:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Agent-Eval-Lab/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                payload = response.read()
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception:
            return None

    def _extract_openai_models(self, payload: Any) -> list[str] | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        models = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("name") or "").strip()
            if model_id:
                models.append(model_id)
        return models

    def _extract_ollama_models(self, payload: Any) -> list[str] | None:
        if not isinstance(payload, dict):
            return None
        models_section = payload.get("models")
        if not isinstance(models_section, list):
            return None
        models = []
        for item in models_section:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("name") or item.get("model") or "").strip()
            if model_id:
                models.append(model_id)
        return models

    def _probe_local_ai_endpoint(self, probe: dict[str, Any]) -> dict[str, Any] | None:
        payload = self._fetch_json(f"{probe['base_url']}{probe['path']}")
        if probe["parser"] == "openai":
            models = self._extract_openai_models(payload)
        else:
            models = self._extract_ollama_models(payload)
        if models is None:
            return None
        return {
            "hint_id": probe.get("hint_id"),
            "label": probe["label"],
            "endpoint": probe["base_url"],
            "port": probe["port"],
            "models": models[:5],
            "model_count": len(models),
        }

    def _detect_local_ai(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for process in self._list_processes():
            process_name = str(process.get("name") or "").strip()
            command_line = str(process.get("command_line") or "").strip()
            haystack = f"{process_name} {command_line}".lower()
            for hint in AI_PROCESS_HINTS:
                if not any(token in haystack for token in hint["match"]):
                    continue
                item = {
                    "id": f"{hint['id']}:process:{process.get('pid')}",
                    "hint_id": hint["id"],
                    "label": hint["label"],
                    "status": "process detected",
                    "pid": process.get("pid"),
                    "process_name": process_name or None,
                    "command_preview": command_line[:180] if command_line else None,
                    "endpoint": None,
                    "port": None,
                    "models": [],
                    "model_count": 0,
                    "api_reachable": False,
                    "ports": list(hint["ports"]),
                }
                items.append(item)
                break

        for probe in AI_ENDPOINT_PROBES:
            endpoint_item = self._probe_local_ai_endpoint(probe)
            if endpoint_item is None:
                continue

            merged = False
            for item in items:
                if probe.get("hint_id") and item.get("hint_id") == probe["hint_id"]:
                    merged = True
                elif probe["port"] in (item.get("ports") or []):
                    merged = True
                if not merged:
                    continue
                item["endpoint"] = endpoint_item["endpoint"]
                item["port"] = endpoint_item["port"]
                item["models"] = endpoint_item["models"]
                item["model_count"] = endpoint_item["model_count"]
                item["api_reachable"] = True
                item["status"] = "process + API"
                break

            if merged:
                continue

            items.append(
                {
                    "id": f"endpoint:{probe['port']}",
                    "hint_id": probe.get("hint_id"),
                    "label": endpoint_item["label"],
                    "status": "API reachable",
                    "pid": None,
                    "process_name": None,
                    "command_preview": None,
                    "endpoint": endpoint_item["endpoint"],
                    "port": endpoint_item["port"],
                    "models": endpoint_item["models"],
                    "model_count": endpoint_item["model_count"],
                    "api_reachable": True,
                    "ports": [probe["port"]],
                }
            )

        items.sort(key=lambda item: (str(item.get("label") or ""), str(item.get("pid") or ""), str(item.get("port") or "")))
        cleaned_items = []
        for item in items:
            cleaned_items.append({key: value for key, value in item.items() if key != "ports"})
        return {
            "items": cleaned_items,
            "count": len(cleaned_items),
            "reachable_count": sum(1 for item in cleaned_items if item.get("api_reachable")),
            "checked_at": now_iso(),
        }

    def list_local_ai(self) -> dict[str, Any]:
        now = time.monotonic()
        cached = self.local_ai_cache or {}
        cached_at = float(cached.get("cached_at", 0.0) or 0.0)
        cached_payload = cached.get("payload")
        if cached_payload is not None and (now - cached_at) < LOCAL_AI_DETECTION_TTL_SEC:
            return dict(cached_payload)
        payload = self._detect_local_ai()
        self.local_ai_cache = {"cached_at": now, "payload": payload}
        return dict(payload)

    def _relative_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())

    def list_configs(self) -> dict[str, Any]:
        default_path = self._default_config_path()
        items = [self._config_summary(path) for path in sorted(self._config_dir().glob("*.json"))]
        for item in items:
            item["selected"] = default_path is not None and item["relative_path"] == self._relative_path(default_path)
        items.sort(key=lambda item: (0 if item["selected"] else 1, item["name"]))
        return {
            "configs": items,
            "default_config": self._relative_path(default_path) if default_path else None,
        }

    def list_task_types(self) -> dict[str, Any]:
        return {
            "task_types": [dict(item) for item in TASK_TYPE_OPTIONS],
            "default_task_type": "auto",
        }

    def list_evolution_modes(self) -> dict[str, Any]:
        return {
            "evolution_modes": [dict(item) for item in EVOLUTION_MODE_OPTIONS],
            "default_evolution_mode": "model_params",
            "heat_map_dimensions": heat_map_dimension_options(),
            "heat_map_task_types": heat_map_task_type_options(),
        }

    def _normalize_task_type(self, raw_value: Any) -> str:
        value = str(raw_value or "auto").strip().lower() or "auto"
        valid_values = {item["value"] for item in TASK_TYPE_OPTIONS}
        if value not in valid_values:
            raise RuntimeError(f"Unsupported task type: {raw_value}")
        return value

    def _normalize_evolution_mode(self, raw_value: Any) -> str:
        value = str(raw_value or "model_params").strip().lower() or "model_params"
        valid_values = {item["value"] for item in EVOLUTION_MODE_OPTIONS}
        if value not in valid_values:
            raise RuntimeError(f"Unsupported evolution mode: {raw_value}")
        return value

    def _load_config_payload(self, path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _normalize_sampling_parameters(self, raw_value: Any, config_payload: dict[str, Any]) -> list[str]:
        provider = sampling_provider_for_config(config_payload)
        supported = {item["id"] for item in sampling_parameters_for_provider(provider)}
        if not supported:
            return []

        values: list[str] = []
        if isinstance(raw_value, list):
            values = [str(item).strip() for item in raw_value]
        elif isinstance(raw_value, str):
            values = [item.strip() for item in raw_value.split(",")]

        selected = []
        for value in values:
            if value and value in supported and value not in selected:
                selected.append(value)
        return selected

    def _normalize_bool(self, raw_value: Any) -> bool | None:
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return raw_value
        normalized = str(raw_value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise RuntimeError(f"Unsupported boolean value: {raw_value}")

    def _normalize_heat_map_values(self, raw_value: Any) -> list[str] | None:
        if raw_value is None:
            return None
        if isinstance(raw_value, list):
            values = [str(item).strip() for item in raw_value]
        else:
            values = [item.strip() for item in str(raw_value).split(",")]
        normalized = [item for item in values if item]
        return normalized or None

    def _normalize_int_list(self, raw_value: Any) -> list[int] | None:
        values = self._normalize_heat_map_values(raw_value)
        if values is None:
            return None
        return [int(item) for item in values]

    def _read_live_status(self) -> dict[str, Any]:
        if not self.live_status_path.exists():
            return {}
        try:
            payload = json.loads(self.live_status_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _estimate_nightly_total_evals(self, config_payload: dict[str, Any], evolution_mode: str) -> int | None:
        config = config_payload if isinstance(config_payload, dict) else {}
        regression_path_value = config.get("regression_suite", {}).get("path")
        if not regression_path_value:
            return None
        regression_path = (self.root / str(regression_path_value)).resolve()
        try:
            regression_suite = json.loads(regression_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        candidate_runs = int(
            config.get("nightly", {}).get(
                "candidate_runs_per_config",
                config.get("nightly", {}).get("candidate_runs", 4),
            )
        )
        if evolution_mode == "heat_map":
            try:
                plan = resolve_heat_map_plan(config)
                per_variant_candidate_runs = int(plan.get("probe_eval_count", 0))
            except Exception:
                plan = None
                per_variant_candidate_runs = candidate_runs
            variant_count = heat_map_candidate_count(config) + 1
        else:
            pool_size = int(config.get("nightly", {}).get("candidate_pool_size", 1))
            include_base = bool(config.get("nightly", {}).get("include_base_config", True))
            variant_count = max(pool_size, 1 if include_base else 1)
            per_variant_candidate_runs = candidate_runs
        case_count = len(regression_suite.get("cases", []))
        total = variant_count * (per_variant_candidate_runs + case_count)
        if evolution_mode == "heat_map":
            try:
                if plan is None:
                    plan = resolve_heat_map_plan(config)
                verify_settings = resolve_heat_map_verification_settings(
                    config,
                    {
                        "top_candidates": [{}] * plan["top_k"],
                        "cell_count": plan["cell_count"],
                    },
                    seed_start=500,
                )
                if verify_settings["enabled"]:
                    verify_target_count = min(verify_settings["top_k"], plan["top_k"]) + 1
                    total += verify_target_count * (verify_settings["candidate_runs_per_config"] + case_count)
            except Exception:
                pass
        return total

    def _resolve_config_path(self, raw_value: str | None) -> Path:
        if raw_value:
            candidate = Path(raw_value)
            candidates = []
            if candidate.is_absolute():
                candidates.append(candidate)
            else:
                candidates.append((self.root / raw_value).resolve())
                candidates.append((self._config_dir() / raw_value).resolve())
            for path in candidates:
                if path.exists():
                    return path
            raise FileNotFoundError(f"Config not found: {raw_value}")

        default_path = self._default_config_path()
        if default_path is None:
            raise FileNotFoundError("No experiment configs were found under configs/experiments.")
        return default_path

    def _close_log_handle_locked(self) -> None:
        if self.log_handle is not None:
            try:
                self.log_handle.close()
            except Exception:
                pass
            self.log_handle = None

    def _write_live_status(self, payload: dict[str, Any]) -> None:
        self.live_status_path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(payload)
        data.setdefault("updated_at", now_iso())
        self.live_status_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_live_event(self, text: str, **extra: Any) -> None:
        self.live_stream_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": now_iso(), "type": "system", "name": "dashboard_control", "text": text, **extra}
        with self.live_stream_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _refresh_process_locked(self) -> None:
        if self.process is None:
            return

        returncode = self.process.poll()
        if returncode is None:
            if self.current is not None:
                self.current["pid"] = self.process.pid
            return

        finished_at = now_iso()
        metadata = dict(self.current or {})
        current_status = self._read_live_status()
        for key in PROGRESS_KEYS:
            if current_status.get(key) is not None:
                metadata[key] = current_status.get(key)
        metadata.update(
            {
                "active": False,
                "finished_at": finished_at,
                "returncode": returncode,
            }
        )
        self.last_result = metadata
        self.process = None
        self.current = None
        self._close_log_handle_locked()
        self._append_live_event(
            f"Dashboard process finished with exit code {returncode}.",
            control_kind=metadata.get("kind"),
            control_config=metadata.get("config_name"),
        )

    def _snapshot_locked(self, message: str | None = None) -> dict[str, Any]:
        self._refresh_process_locked()
        current = dict(self.current) if self.current else None
        last_result = dict(self.last_result) if self.last_result else None
        data = {
            **self.list_configs(),
            **self.list_task_types(),
            **self.list_evolution_modes(),
            "heat_map_dimensions": heat_map_dimension_options(),
            "active": bool(current),
            "current": current,
            "last_result": last_result,
            "diagnostics": self._diagnostics_snapshot(),
        }
        if message:
            data["message"] = message
        return data

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            payload = self._snapshot_locked()
        return self._with_local_ai(payload)

    def _launch_locked(
        self,
        *,
        kind: str,
        config_path: Path,
        extra_args: list[str],
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        self._refresh_process_locked()
        if self.process is not None:
            raise RuntimeError("Another experiment is already running. Stop it before starting a new one.")

        script_name_by_kind = {
            "single": "run_single.py",
            "suite": "run_suite.py",
            "nightly": "run_nightly.py",
            "relayer_scan": "run_relayer_scan.py",
        }
        script_name = script_name_by_kind.get(kind)
        if script_name is None:
            raise RuntimeError(f"Unsupported experiment kind: {kind}")
        script_path = self.root / "scripts" / script_name
        summary = self._config_summary(config_path)
        command = [sys.executable, str(script_path), "--config", str(config_path), *extra_args]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = self.logs_dir / f"{kind}_{stamp}.log"

        self._close_log_handle_locked()
        self.log_handle = log_path.open("w", encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.process = subprocess.Popen(
            command,
            cwd=self.root,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )

        self.current = {
            "kind": kind,
            "pid": self.process.pid,
            "command": command,
            "config_name": summary["name"],
            "config_id": summary["config_id"],
            "config_path": str(config_path.resolve()),
            "config_relative": summary["relative_path"],
            "runner": summary["runner"],
            "started_at": now_iso(),
            "log_path": str(log_path.resolve()),
            **extras,
        }
        self.last_result = None

        run_kind = kind
        suite_progress_target = extras.get("runs") if kind == "suite" else None
        suite_progress_current = 0 if kind == "suite" else None
        progress_target = extras.get("estimated_total_evals")
        if kind == "suite":
            progress_target = extras.get("runs")
        elif kind == "single":
            progress_target = 1
        elif kind == "relayer_scan":
            progress_target = extras.get("estimated_total_evals") or extras.get("candidate_count")
        progress_current = 0 if kind in {"suite", "nightly", "relayer_scan"} else 1
        status_payload = {
            "run_id": None,
            "status": "launching",
            "task_id": None,
            "config_id": summary["config_id"],
            "runner": "dashboard_control",
            "current_tool": "process_launch",
            "last_error": None,
            "step_count": 0,
            "max_steps": progress_target or 1,
            "elapsed_sec": 0,
            "updated_at": now_iso(),
            "run_kind": run_kind,
            "suite_id": None,
            "case_id": None,
            "task_type": extras.get("task_type", "auto"),
            "control_active": True,
            "control_pid": self.process.pid,
            "control_command": " ".join(command),
        }
        if suite_progress_current is not None:
            status_payload["suite_progress_current"] = suite_progress_current
        if suite_progress_target is not None:
            status_payload["suite_progress_target"] = suite_progress_target
        if suite_progress_current is not None and suite_progress_target is not None:
            status_payload["suite_progress_text"] = f"{suite_progress_current}/{suite_progress_target}"
        if progress_target is not None:
            status_payload["progress_target"] = progress_target
            status_payload["progress_current"] = progress_current
            status_payload["progress_text"] = f"{progress_current}/{progress_target}"
        self._write_live_status(
            status_payload
        )
        self._append_live_event(
            f"Started {kind} experiment from dashboard using {summary['name']}.",
            control_kind=kind,
            control_config=summary["name"],
        )
        return self._snapshot_locked(message=f"Started {kind} experiment.")

    def start_single(self, payload: dict[str, Any]) -> dict[str, Any]:
        config_path = self._resolve_config_path(payload.get("config"))
        extra_args: list[str] = []
        task_type = self._normalize_task_type(payload.get("task_type"))
        extra_args.extend(["--task-type", task_type])
        extras: dict[str, Any] = {"task_type": task_type}
        seed = payload.get("seed")
        if seed is not None and str(seed).strip() != "":
            seed_value = int(seed)
            extra_args.extend(["--seed", str(seed_value)])
            extras["seed"] = seed_value

        with self.lock:
            payload = self._launch_locked(kind="single", config_path=config_path, extra_args=extra_args, extras=extras)
        return self._with_local_ai(payload)

    def start_suite(self, payload: dict[str, Any]) -> dict[str, Any]:
        config_path = self._resolve_config_path(payload.get("config"))
        runs = int(payload.get("runs", 3))
        if runs < 1:
            raise RuntimeError("Suite runs must be at least 1.")

        task_type = self._normalize_task_type(payload.get("task_type"))
        extra_args = ["--runs", str(runs), "--task-type", task_type]
        extras: dict[str, Any] = {"runs": runs, "task_type": task_type}
        seed_start = payload.get("seed_start")
        if seed_start is not None and str(seed_start).strip() != "":
            seed_start_value = int(seed_start)
            extra_args.extend(["--seed-start", str(seed_start_value)])
            extras["seed_start"] = seed_start_value

        with self.lock:
            payload = self._launch_locked(kind="suite", config_path=config_path, extra_args=extra_args, extras=extras)
        return self._with_local_ai(payload)

    def start_nightly(self, payload: dict[str, Any]) -> dict[str, Any]:
        config_path = self._resolve_config_path(payload.get("config"))
        config_payload = self._load_config_payload(config_path)
        extra_args: list[str] = []
        evolution_mode = self._normalize_evolution_mode(payload.get("evolution_mode"))
        if evolution_mode in {"architecture_program", "heat_map"} and not supports_architecture_evolution(config_payload):
            raise RuntimeError(f"{evolution_mode} evolution requires a config supported by architecture-aware runners.")
        extra_args.extend(["--evolution-mode", evolution_mode])
        sampling_parameters = self._normalize_sampling_parameters(payload.get("sampling_parameters"), config_payload)
        if evolution_mode == "model_params":
            sampling_provider = sampling_provider_for_config(config_payload)
            if sampling_provider is None:
                raise RuntimeError("Model-parameter evolution requires a llama_cpp_agent config with provider=ollama or provider=llama-cpp.")
            if not sampling_parameters:
                sampling_parameters = selected_sampling_parameters_for_config(config_payload)
            if not sampling_parameters:
                raise RuntimeError("Select at least one sampling parameter for model-parameter evolution.")
            extra_args.extend(["--sampling-parameters", ",".join(sampling_parameters)])
        else:
            sampling_provider = sampling_provider_for_config(config_payload)

        heat_map_start_layer_min = payload.get("heat_map_start_layer_min")
        heat_map_end_layer_max = payload.get("heat_map_end_layer_max")
        heat_map_min_block_len = payload.get("heat_map_min_block_len")
        heat_map_max_block_len = payload.get("heat_map_max_block_len")
        heat_map_repeat_count = payload.get("heat_map_repeat_count")
        heat_map_probe_a_task_type = payload.get("heat_map_probe_a_task_type")
        heat_map_probe_a_seeds = self._normalize_int_list(payload.get("heat_map_probe_a_seeds"))
        heat_map_probe_b_task_type = payload.get("heat_map_probe_b_task_type")
        heat_map_probe_b_seeds = self._normalize_int_list(payload.get("heat_map_probe_b_seeds"))
        heat_map_top_k = payload.get("heat_map_top_k")
        heat_map_verify_enabled = self._normalize_bool(payload.get("heat_map_verify_enabled"))
        heat_map_verify_runs = payload.get("heat_map_verify_runs")
        verify_overrides = {
            "enabled": heat_map_verify_enabled,
            "candidate_runs_per_config": int(heat_map_verify_runs) if heat_map_verify_runs not in (None, "") else None,
        }
        if evolution_mode == "heat_map":
            config_payload = apply_heat_map_overrides(
                config_payload,
                start_layer_min=int(heat_map_start_layer_min) if heat_map_start_layer_min not in (None, "") else None,
                end_layer_max=int(heat_map_end_layer_max) if heat_map_end_layer_max not in (None, "") else None,
                min_block_len=int(heat_map_min_block_len) if heat_map_min_block_len not in (None, "") else None,
                max_block_len=int(heat_map_max_block_len) if heat_map_max_block_len not in (None, "") else None,
                repeat_count=int(heat_map_repeat_count) if heat_map_repeat_count not in (None, "") else None,
                probe_a_task_type=str(heat_map_probe_a_task_type).strip().lower() if heat_map_probe_a_task_type else None,
                probe_a_seeds=heat_map_probe_a_seeds,
                probe_b_task_type=str(heat_map_probe_b_task_type).strip().lower() if heat_map_probe_b_task_type else None,
                probe_b_seeds=heat_map_probe_b_seeds,
                top_k=int(heat_map_top_k) if heat_map_top_k not in (None, "") else None,
                verify_overrides=verify_overrides,
            )
            try:
                resolved_plan = resolve_heat_map_plan(config_payload)
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc
            if heat_map_start_layer_min not in (None, ""):
                extra_args.extend(["--heat-map-start-layer-min", str(int(heat_map_start_layer_min))])
            if heat_map_end_layer_max not in (None, ""):
                extra_args.extend(["--heat-map-end-layer-max", str(int(heat_map_end_layer_max))])
            if heat_map_min_block_len not in (None, ""):
                extra_args.extend(["--heat-map-min-block-len", str(int(heat_map_min_block_len))])
            if heat_map_max_block_len not in (None, ""):
                extra_args.extend(["--heat-map-max-block-len", str(int(heat_map_max_block_len))])
            if heat_map_repeat_count not in (None, ""):
                extra_args.extend(["--heat-map-repeat-count", str(int(heat_map_repeat_count))])
            if heat_map_probe_a_task_type:
                extra_args.extend(["--heat-map-probe-a-task-type", str(heat_map_probe_a_task_type).strip().lower()])
            if heat_map_probe_a_seeds:
                extra_args.extend(["--heat-map-probe-a-seeds", ",".join(str(item) for item in heat_map_probe_a_seeds)])
            if heat_map_probe_b_task_type:
                extra_args.extend(["--heat-map-probe-b-task-type", str(heat_map_probe_b_task_type).strip().lower()])
            if heat_map_probe_b_seeds:
                extra_args.extend(["--heat-map-probe-b-seeds", ",".join(str(item) for item in heat_map_probe_b_seeds)])
            if heat_map_top_k not in (None, ""):
                extra_args.extend(["--heat-map-top-k", str(int(heat_map_top_k))])
            if heat_map_verify_enabled is False:
                extra_args.append("--disable-heat-map-verify")
            if heat_map_verify_runs not in (None, ""):
                extra_args.extend(["--heat-map-verify-runs", str(int(heat_map_verify_runs))])
        else:
            resolved_plan = resolve_heat_map_plan(config_payload) if supports_architecture_evolution(config_payload) else None
        extras: dict[str, Any] = {
            "estimated_total_evals": self._estimate_nightly_total_evals(config_payload, evolution_mode),
            "evolution_mode": evolution_mode,
            "sampling_provider": sampling_provider,
            "sampling_parameters": sampling_parameters,
        }
        if evolution_mode == "heat_map":
            extras["heat_map_plan"] = resolved_plan
        seed_start = payload.get("seed_start")
        if seed_start is not None and str(seed_start).strip() != "":
            seed_start_value = int(seed_start)
            extra_args.extend(["--seed-start", str(seed_start_value)])
            extras["seed_start"] = seed_start_value

        with self.lock:
            payload = self._launch_locked(kind="nightly", config_path=config_path, extra_args=extra_args, extras=extras)
        return self._with_local_ai(payload)

    def start_relayer_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        config_path = self._resolve_config_path(payload.get("config"))
        config_payload = self._load_config_payload(config_path)
        relayer_summary = summarize_relayer_config(config_payload)
        if not relayer_summary.get("scan_supported"):
            raise RuntimeError(relayer_summary.get("scan_error") or "Relayer scan is not configured for this config.")

        extra_args: list[str] = []
        requested_max_candidates = payload.get("max_candidates")
        candidate_count = relayer_summary.get("scan_candidate_count")
        seed_start_value = 500
        max_workers_value = int(payload.get("max_workers", 1) or 1)
        if max_workers_value < 1:
            raise RuntimeError("Relayer scan max_workers must be at least 1.")
        extra_args.extend(["--max-workers", str(max_workers_value)])
        resume_requested = bool(self._normalize_bool(payload.get("resume")) or False)
        skip_completed_requested = bool(self._normalize_bool(payload.get("skip_completed")) or False)
        output_dir_value = payload.get("output_dir")
        resolved_output_dir: str | None = None
        if output_dir_value not in (None, ""):
            raw_path = Path(str(output_dir_value))
            resolved_output_dir = str(raw_path if raw_path.is_absolute() else (self.root / raw_path).resolve())
            extra_args.extend(["--output-dir", resolved_output_dir])
        if resume_requested:
            extra_args.append("--resume")
        if skip_completed_requested:
            extra_args.append("--skip-completed")
        requested_seed_start = payload.get("seed_start")
        if requested_seed_start not in (None, ""):
            seed_start_value = int(requested_seed_start)
            extra_args.extend(["--seed-start", str(seed_start_value)])
        if requested_max_candidates not in (None, ""):
            max_candidates = int(requested_max_candidates)
            if max_candidates < 1:
                raise RuntimeError("Relayer scan max_candidates must be at least 1.")
            extra_args.extend(["--max-candidates", str(max_candidates)])
            if candidate_count is not None:
                candidate_count = min(int(candidate_count), max_candidates)

        extras: dict[str, Any] = {
            "candidate_count": candidate_count,
            "estimated_total_evals": estimate_relayer_scan_total_evals(
                config_payload,
                int(candidate_count or 0),
                seed_start=seed_start_value,
            )
            if candidate_count is not None
            else None,
            "relayer_mode": relayer_summary.get("mode"),
            "relayer_scan_backend": relayer_summary.get("scan_backend"),
            "relayer_scan_runtime_mode": relayer_summary.get("scan_runtime_mode"),
            "relayer_runtime_patch_supported": relayer_summary.get("runtime_patch_supported"),
            "relayer_verification_capable": relayer_summary.get("verification_capable"),
            "relayer_scan_note": relayer_summary.get("scan_note"),
            "seed_start": seed_start_value,
            "max_workers": max_workers_value,
            "resume": resume_requested,
            "skip_completed": skip_completed_requested,
        }
        if requested_max_candidates not in (None, ""):
            extras["max_candidates"] = int(requested_max_candidates)
        if resolved_output_dir is not None:
            extras["output_dir"] = resolved_output_dir

        with self.lock:
            payload = self._launch_locked(
                kind="relayer_scan",
                config_path=config_path,
                extra_args=extra_args,
                extras=extras,
            )
        return self._with_local_ai(payload)

    def stop(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_process_locked()
            if self.process is None or self.current is None:
                payload = self._snapshot_locked(message="No active experiment to stop.")
            else:
                metadata = dict(self.current)
                pid = self.process.pid

                if os.name == "nt":
                    _run_subprocess(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)
                else:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()

                try:
                    self.process.wait(timeout=5)
                except Exception:
                    pass

                returncode = self.process.poll()
                stopped_at = now_iso()
                current_status = self._read_live_status()
                for key in PROGRESS_KEYS:
                    if current_status.get(key) is not None:
                        metadata[key] = current_status.get(key)
                metadata.update(
                    {
                        "active": False,
                        "stopped_by_user": True,
                        "stopped_at": stopped_at,
                        "returncode": returncode,
                    }
                )
                self.last_result = metadata
                self.process = None
                self.current = None
                self._close_log_handle_locked()

                status_payload = {
                    "run_id": None,
                    "status": "stopped_by_user",
                    "task_id": None,
                    "config_id": metadata.get("config_id"),
                    "runner": "dashboard_control",
                    "current_tool": None,
                    "last_error": "Stopped from dashboard",
                    "step_count": 0,
                    "max_steps": metadata.get("progress_target") or metadata.get("suite_progress_target") or 1,
                    "elapsed_sec": 0,
                    "updated_at": stopped_at,
                    "run_kind": metadata.get("kind"),
                    "suite_id": None,
                    "case_id": None,
                    "task_type": metadata.get("task_type"),
                    "control_active": False,
                    "control_pid": None,
                    "control_command": " ".join(metadata.get("command", [])),
                }
                for key in PROGRESS_KEYS:
                    if metadata.get(key) is not None:
                        status_payload[key] = metadata.get(key)
                self._write_live_status(status_payload)
                self._append_live_event(
                    "Stopped active experiment from dashboard.",
                    control_kind=metadata.get("kind"),
                    control_config=metadata.get("config_name"),
                )
                payload = self._snapshot_locked(message="Stopped active experiment.")
        return self._with_local_ai(payload)


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class, controller: RunController) -> None:
        super().__init__(server_address, handler_class)
        self.controller = controller


class DashboardHandler(SimpleHTTPRequestHandler):
    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/ping":
            self._send_json({"ok": True, "ts": now_iso()})
            return
        if parsed.path == "/api/control":
            self._send_json(self.server.controller.snapshot())
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        try:
            if parsed.path == "/api/start-run":
                response = self.server.controller.start_single(payload)
            elif parsed.path == "/api/start-suite":
                response = self.server.controller.start_suite(payload)
            elif parsed.path == "/api/start-nightly":
                response = self.server.controller.start_nightly(payload)
            elif parsed.path == "/api/start-relayer-scan":
                response = self.server.controller.start_relayer_scan(payload)
            elif parsed.path == "/api/stop":
                response = self.server.controller.stop()
            else:
                self._send_json({"error": "Not found."}, status=404)
                return
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        except RuntimeError as exc:
            data = self.server.controller.snapshot()
            data["error"] = str(exc)
            self._send_json(data, status=409)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)
            return

        self._send_json(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local dashboard over HTTP.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--pid-file", default=None)
    args = parser.parse_args()

    os.chdir(ROOT)
    pid_file = Path(args.pid_file).resolve() if args.pid_file else None
    _write_pid_file(pid_file)
    controller = RunController(ROOT)
    server = DashboardServer(("127.0.0.1", args.port), DashboardHandler, controller)
    print(f"Serving dashboard at http://127.0.0.1:{args.port}/dashboard.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _remove_pid_file(pid_file)


if __name__ == "__main__":
    main()
