from __future__ import annotations

import csv
import math
import struct
import zlib
from pathlib import Path
from typing import Any

from storage.history_writer import write_json


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _iter_cells(summary: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for row in summary.get("matrix", []):
        for cell in row.get("cells", []):
            if not isinstance(cell, dict):
                continue
            normalized = dict(cell)
            normalized.setdefault("status", "ok" if "delta_suite_score" in normalized else "not_run")
            cells.append(normalized)
    return cells


def _csv_value(cell: dict[str, Any], key: str) -> Any:
    if cell.get("status") == "not_run":
        return ""
    return cell.get(key, "")


def _write_matrix_csv(path: Path, summary: dict[str, Any]) -> None:
    x_values = summary.get("x_values", [])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([summary.get("y_axis", "y_axis")] + [str(value) for value in x_values])
        for row in summary.get("matrix", []):
            writer.writerow(
                [str(row.get("y_value"))]
                + [str(_csv_value(cell, "delta_suite_score")) for cell in row.get("cells", [])]
            )


def _write_cells_csv(path: Path, summary: dict[str, Any]) -> None:
    fields = [
        "config_id",
        "x_axis",
        "x_label",
        "x_value",
        "y_axis",
        "y_label",
        "y_value",
        "suite_score_c",
        "fitness",
        "regression_pass_rate",
        "honesty_score",
        "delta_suite_score",
        "delta_fitness",
        "improved_vs_baseline",
        "status",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in _iter_cells(summary):
            writer.writerow({field: cell.get(field, "") for field in fields})


def _write_top_candidates_csv(path: Path, summary: dict[str, Any]) -> None:
    fields = [
        "config_id",
        "x_value",
        "y_value",
        "suite_score_c",
        "fitness",
        "regression_pass_rate",
        "honesty_score",
        "delta_suite_score",
        "delta_fitness",
        "improved_vs_baseline",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in summary.get("top_candidates", []):
            writer.writerow({field: cell.get(field, "") for field in fields})


def _set_pixel(buffer: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    offset = (y * width + x) * 4
    buffer[offset : offset + 4] = bytes(color)


def _fill_rect(
    buffer: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int, int],
) -> None:
    for y in range(y0, y0 + rect_height):
        for x in range(x0, x0 + rect_width):
            _set_pixel(buffer, width, height, x, y, color)


def _stroke_rect(
    buffer: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int, int],
    thickness: int = 1,
) -> None:
    for index in range(thickness):
        for x in range(x0 + index, x0 + rect_width - index):
            _set_pixel(buffer, width, height, x, y0 + index, color)
            _set_pixel(buffer, width, height, x, y0 + rect_height - 1 - index, color)
        for y in range(y0 + index, y0 + rect_height - index):
            _set_pixel(buffer, width, height, x0 + index, y, color)
            _set_pixel(buffer, width, height, x0 + rect_width - 1 - index, y, color)


def _delta_color(delta: float | None, max_abs_delta: float) -> tuple[int, int, int, int]:
    if delta is None or math.isnan(delta):
        return (235, 235, 235, 255)
    if max_abs_delta <= 1e-12:
        return (240, 240, 240, 255)

    magnitude = min(abs(delta) / max_abs_delta, 1.0)
    base = 245
    if delta >= 0:
        return (
            int(base - (70 * magnitude)),
            int(base - (25 * magnitude)),
            int(base - (150 * magnitude)),
            255,
        )
    return (
        int(base - (135 * magnitude)),
        int(base - (55 * magnitude)),
        int(base - (10 * magnitude)),
        255,
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _encode_png(
    *,
    width: int,
    height: int,
    rgba: bytearray,
    metadata: dict[str, str],
) -> bytes:
    rows = bytearray()
    for y in range(height):
        start = y * width * 4
        end = start + (width * 4)
        rows.append(0)
        rows.extend(rgba[start:end])

    chunks = [
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
    ]
    for key, value in metadata.items():
        chunks.append(_png_chunk(b"tEXt", f"{key}\x00{value}".encode("latin-1", errors="replace")))
    chunks.append(_png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9)))
    chunks.append(_png_chunk(b"IEND", b""))
    return PNG_SIGNATURE + b"".join(chunks)


def _write_heatmap_png(path: Path, summary: dict[str, Any], title: str) -> None:
    x_values = summary.get("x_values", [])
    y_values = summary.get("y_values", [])
    grid_width = max(1, len(x_values))
    grid_height = max(1, len(y_values))
    cell_size = 36
    padding = 18
    legend_width = 18
    width = padding * 3 + (grid_width * cell_size) + legend_width + 12
    height = padding * 2 + (grid_height * cell_size)
    rgba = bytearray([248, 247, 244, 255] * width * height)

    cells = _iter_cells(summary)
    deltas = [
        float(cell["delta_suite_score"])
        for cell in cells
        if cell.get("status") != "not_run" and cell.get("delta_suite_score") is not None
    ]
    max_abs_delta = max((abs(value) for value in deltas), default=0.0)

    baseline = summary.get("baseline", {})
    best_cell = summary.get("best_cell", {})
    grid_x = padding
    grid_y = padding

    for row_index, row in enumerate(summary.get("matrix", [])):
        for col_index, cell in enumerate(row.get("cells", [])):
            x0 = grid_x + (col_index * cell_size)
            y0 = grid_y + (row_index * cell_size)
            delta = cell.get("delta_suite_score")
            if delta is not None:
                delta = float(delta)
            color = _delta_color(delta, max_abs_delta)
            _fill_rect(rgba, width, height, x0, y0, cell_size - 2, cell_size - 2, color)
            _stroke_rect(rgba, width, height, x0, y0, cell_size - 1, cell_size - 1, (255, 255, 255, 255))
            if (
                cell.get("x_value") == baseline.get("x_value")
                and cell.get("y_value") == baseline.get("y_value")
            ):
                _stroke_rect(rgba, width, height, x0 + 1, y0 + 1, cell_size - 4, cell_size - 4, (36, 36, 36, 255), 2)
            if cell.get("config_id") and cell.get("config_id") == best_cell.get("config_id"):
                _stroke_rect(rgba, width, height, x0 + 4, y0 + 4, cell_size - 10, cell_size - 10, (218, 165, 32, 255), 2)

    legend_x = grid_x + (grid_width * cell_size) + padding
    legend_y = grid_y
    legend_height = grid_height * cell_size
    for offset in range(legend_height):
        ratio = 1.0 - (offset / max(1, legend_height - 1))
        delta = ((ratio * 2.0) - 1.0) * max_abs_delta
        color = _delta_color(delta, max_abs_delta)
        _fill_rect(rgba, width, height, legend_x, legend_y + offset, legend_width, 1, color)
    _stroke_rect(rgba, width, height, legend_x, legend_y, legend_width, legend_height, (255, 255, 255, 255))

    metadata = {
        "Title": title,
        "Description": (
            f"suite_id={summary.get('suite_id', '-')}; "
            f"axes={summary.get('y_label', summary.get('y_axis', '-'))} x "
            f"{summary.get('x_label', summary.get('x_axis', '-'))}"
        ),
        "Software": "Agent Eval Lab heat-map builder",
    }
    path.write_bytes(_encode_png(width=width, height=height, rgba=rgba, metadata=metadata))


def build_heat_map_artifacts(
    *,
    output_dir: Path,
    suite_id: str,
    heat_map_summary: dict[str, Any],
    created_at: str,
    base_config_id: str,
    selected_config_id: str,
    status: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    matrix_path = output_dir / "matrix.csv"
    cells_path = output_dir / "cells.csv"
    top_candidates_json_path = output_dir / "top_candidates.json"
    top_candidates_csv_path = output_dir / "top_candidates.csv"
    summary_path = output_dir / "summary.json"
    png_path = output_dir / "heatmap.png"
    manifest_path = output_dir / "manifest.json"

    _write_matrix_csv(matrix_path, heat_map_summary)
    _write_cells_csv(cells_path, heat_map_summary)
    _write_top_candidates_csv(top_candidates_csv_path, heat_map_summary)
    write_json(top_candidates_json_path, {"top_candidates": heat_map_summary.get("top_candidates", [])})
    write_json(summary_path, heat_map_summary)
    _write_heatmap_png(
        png_path,
        {**heat_map_summary, "suite_id": suite_id},
        title=f"Agent Eval Lab Heat Map | {base_config_id} | {suite_id}",
    )

    manifest = {
        "suite_id": suite_id,
        "created_at": created_at,
        "status": status,
        "base_config_id": base_config_id,
        "selected_config_id": selected_config_id,
        "x_axis": heat_map_summary.get("x_axis"),
        "x_label": heat_map_summary.get("x_label"),
        "y_axis": heat_map_summary.get("y_axis"),
        "y_label": heat_map_summary.get("y_label"),
        "cell_count": heat_map_summary.get("cell_count"),
        "improved_cell_count": heat_map_summary.get("improved_cell_count"),
        "matrix_path": str(matrix_path.resolve()),
        "cells_path": str(cells_path.resolve()),
        "top_candidates_json_path": str(top_candidates_json_path.resolve()),
        "top_candidates_csv_path": str(top_candidates_csv_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "png_path": str(png_path.resolve()),
    }
    write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path.resolve())
    manifest["output_dir"] = str(output_dir.resolve())
    return manifest
