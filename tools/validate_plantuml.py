#!/usr/bin/env python3
"""PlantUML アーキテクチャ成果物の構造と安全性を検査する。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ALLOWED_EVIDENCE = {"code", "config", "test", "document", "inferred"}
ALLOWED_CERTAINTY = {"confirmed", "inferred", "runtime-unverified"}
ALLOWED_MESSAGE_ROLES = {"input", "output"}
REQUIRED_TOP_LEVEL = {"schema_version", "scope", "nodes", "edges", "zones", "unresolved"}
REQUIRED_COVERAGE_SECTIONS = {"source_files", "classes", "methods", "functions"}
REQUIRED_NODE_FIELDS = {"id", "kind", "name", "role", "source_refs", "evidence", "certainty"}
REQUIRED_EDGE_FIELDS = {
    "id",
    "from",
    "to",
    "relation",
    "purpose",
    "source_refs",
    "evidence",
    "certainty",
}
REQUIRED_ZONE_FIELDS = {
    "id",
    "name",
    "boundary_reason",
    "source_refs",
    "evidence",
    "certainty",
}

START_RE = re.compile(r"(?im)^\s*@start\w+\b")
END_RE = re.compile(r"(?im)^\s*@end\w+\b")
TITLE_RE = re.compile(r"(?im)^\s*title(?:\s|$)")
LEGEND_RE = re.compile(r"(?im)^\s*legend(?:\s|$)")
SEQUENCE_PARTICIPANT_RE = re.compile(r"(?im)^\s*participant(?:\s|$)")
AUTONUMBER_RE = re.compile(r"(?im)^\s*autonumber(?:\s|$)")
INCLUDE_RE = re.compile(
    r"(?im)^\s*!(includeurl|include|include_once|include_many|import)\s+(.+?)\s*$"
)
REMOTE_RE = re.compile(r"(?i)https?://")
REMOTE_IMAGE_RE = re.compile(r"(?i)<img\s*(?::|src\s*=\s*[\"'])\s*https?://")
SECRET_RE = re.compile(
    r"(?ix)"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|private[_-]?key)"
    r"\s*[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_+/=-]{16,}"
)


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalized_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def validate_source_refs(value: Any, location: str, findings: Findings) -> None:
    if not isinstance(value, list) or not value:
        findings.error(f"{location}: source_refs は空でない配列が必要です")
        return

    for index, source_ref in enumerate(value):
        item_location = f"{location}.source_refs[{index}]"
        if not is_non_empty_string(source_ref):
            findings.error(f"{item_location}: 空でない文字列が必要です")
            continue

        path_part = source_ref.split(":", 1)[0]
        path = Path(path_part)
        if path.is_absolute() or ".." in path.parts:
            findings.error(
                f"{item_location}: リポジトリ相対パスを使用してください: {source_ref}"
            )


def validate_evidence(item: dict[str, Any], location: str, findings: Findings) -> None:
    evidence = item.get("evidence")
    certainty = item.get("certainty")
    if evidence not in ALLOWED_EVIDENCE:
        findings.error(
            f"{location}.evidence: 許可値は {sorted(ALLOWED_EVIDENCE)} です"
        )
    if certainty not in ALLOWED_CERTAINTY:
        findings.error(
            f"{location}.certainty: 許可値は {sorted(ALLOWED_CERTAINTY)} です"
        )


def require_fields(
    item: Any,
    required: set[str],
    location: str,
    findings: Findings,
) -> bool:
    if not isinstance(item, dict):
        findings.error(f"{location}: オブジェクトが必要です")
        return False

    missing = sorted(required - item.keys())
    if missing:
        findings.error(f"{location}: 必須フィールドがありません: {', '.join(missing)}")
        return False
    return True


def validate_nonnegative_integer(
    value: Any,
    location: str,
    findings: Findings,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        findings.error(f"{location}: 0 以上の整数が必要です")
        return None
    return value


def validate_exclusions(value: Any, location: str, findings: Findings) -> int:
    if not isinstance(value, list):
        findings.error(f"{location}: 配列が必要です")
        return 0

    total = 0
    for index, exclusion in enumerate(value):
        item_location = f"{location}[{index}]"
        if not isinstance(exclusion, dict):
            findings.error(f"{item_location}: オブジェクトが必要です")
            continue

        count = validate_nonnegative_integer(
            exclusion.get("count"),
            f"{item_location}.count",
            findings,
        )
        if count == 0:
            findings.error(f"{item_location}.count: 除外件数は 1 以上にしてください")
        elif count is not None:
            total += count

        if not is_non_empty_string(exclusion.get("reason")):
            findings.error(f"{item_location}.reason: 技術的な除外理由が必要です")
        if not any(
            is_non_empty_string(exclusion.get(field))
            for field in ("path", "pattern", "name")
        ):
            findings.error(
                f"{item_location}: path、pattern、name のいずれかが必要です"
            )
    return total


def validate_coverage(
    value: Any,
    nodes: list[Any],
    model_path: Path,
    findings: Findings,
) -> None:
    if not isinstance(value, dict):
        findings.error(f"{model_path}: coverage はオブジェクトである必要があります")
        return

    missing = sorted(REQUIRED_COVERAGE_SECTIONS - value.keys())
    if missing:
        findings.error(
            f"{model_path}: coverage の必須区分がありません: {', '.join(missing)}"
        )

    source_files = value.get("source_files")
    if isinstance(source_files, dict):
        discovered = validate_nonnegative_integer(
            source_files.get("discovered"),
            "coverage.source_files.discovered",
            findings,
        )
        analyzed = validate_nonnegative_integer(
            source_files.get("analyzed"),
            "coverage.source_files.analyzed",
            findings,
        )
        excluded = validate_exclusions(
            source_files.get("excluded"),
            "coverage.source_files.excluded",
            findings,
        )
        if discovered is not None and analyzed is not None:
            if discovered != analyzed + excluded:
                findings.error(
                    "coverage.source_files: discovered は analyzed と除外件数の合計に"
                    "一致する必要があります"
                )
    else:
        findings.error("coverage.source_files: オブジェクトが必要です")

    kind_by_section = {
        "classes": "class",
        "methods": "method",
        "functions": "function",
    }
    for section, kind in kind_by_section.items():
        item = value.get(section)
        location = f"coverage.{section}"
        if not isinstance(item, dict):
            findings.error(f"{location}: オブジェクトが必要です")
            continue

        discovered = validate_nonnegative_integer(
            item.get("discovered"), f"{location}.discovered", findings
        )
        modeled = validate_nonnegative_integer(
            item.get("modeled"), f"{location}.modeled", findings
        )
        diagrammed = validate_nonnegative_integer(
            item.get("diagrammed"), f"{location}.diagrammed", findings
        )
        excluded = validate_exclusions(
            item.get("excluded"), f"{location}.excluded", findings
        )

        if discovered is not None and modeled is not None:
            if discovered != modeled + excluded:
                findings.error(
                    f"{location}: discovered は modeled と除外件数の合計に"
                    "一致する必要があります"
                )
        if modeled is not None and diagrammed is not None and modeled != diagrammed:
            findings.error(f"{location}: modeled と diagrammed が一致していません")

        node_count = sum(
            1 for node in nodes if isinstance(node, dict) and node.get("kind") == kind
        )
        if modeled is not None and modeled != node_count:
            findings.error(
                f"{location}.modeled: nodes の {kind} 件数 {node_count} と一致しません"
            )


def validate_exchange_edges(
    edges: list[Any],
    diagram_text: str,
    findings: Findings,
) -> None:
    exchanges: dict[str, list[tuple[int, dict[str, Any]]]] = {}

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue
        location = f"edges[{index}]"
        exchange_id = edge.get("exchange_id")
        message_role = edge.get("message_role")
        if exchange_id is None and message_role is None:
            continue

        if not is_non_empty_string(exchange_id):
            findings.error(
                f"{location}.exchange_id: 双方向通信には空でない文字列が必要です"
            )
            continue
        if message_role not in ALLOWED_MESSAGE_ROLES:
            findings.error(
                f"{location}.message_role: 許可値は "
                f"{sorted(ALLOWED_MESSAGE_ROLES)} です"
            )

        data = edge.get("data")
        if not isinstance(data, list) or not data:
            findings.error(
                f"{location}.data: 入力または出力を表す空でない配列が必要です"
            )
        elif any(not is_non_empty_string(item) for item in data):
            findings.error(f"{location}.data: 各項目は空でない文字列が必要です")

        exchanges.setdefault(exchange_id, []).append((index, edge))

    for exchange_id, members in exchanges.items():
        location = f"exchange_id {exchange_id}"
        if len(members) != 2:
            findings.error(f"{location}: input と output の 2 エッジが必要です")
            continue

        by_role = {edge.get("message_role"): (index, edge) for index, edge in members}
        if set(by_role) != ALLOWED_MESSAGE_ROLES:
            findings.error(f"{location}: input と output を 1 件ずつ指定してください")
            continue

        _input_index, input_edge = by_role["input"]
        _output_index, output_edge = by_role["output"]
        if not (
            input_edge.get("from") == output_edge.get("to")
            and input_edge.get("to") == output_edge.get("from")
        ):
            findings.error(f"{location}: input と output の向きが反対ではありません")

        for role, (index, edge) in by_role.items():
            edge_id = edge.get("id")
            marker = "入力:" if role == "input" else "出力:"
            if not is_non_empty_string(edge_id):
                continue
            matching_lines = [
                line for line in diagram_text.splitlines() if edge_id in line
            ]
            if not matching_lines:
                findings.error(f"edges[{index}]: PlantUML 図に {edge_id} がありません")
            elif not any(marker in line for line in matching_lines):
                findings.error(
                    f"edges[{index}]: 図の矢印ラベルに {marker} がありません"
                )


def validate_model(
    model_path: Path,
    catalog_text: str,
    diagram_text: str,
    diagram_texts: dict[str, str],
    findings: Findings,
) -> None:
    try:
        data = json.loads(model_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        findings.error(f"{model_path}: JSON を読めません: {exc}")
        return

    if not isinstance(data, dict):
        findings.error(f"{model_path}: 最上位はオブジェクトである必要があります")
        return

    missing = sorted(REQUIRED_TOP_LEVEL - data.keys())
    if missing:
        findings.error(f"{model_path}: 必須フィールドがありません: {', '.join(missing)}")

    schema_version = data.get("schema_version")
    if schema_version not in {1, 2}:
        findings.error(f"{model_path}: schema_version は 1 または 2 が必要です")
    if schema_version == 2 and "coverage" not in data:
        findings.error(f"{model_path}: schema_version 2 には coverage が必要です")

    scope = data.get("scope")
    if not isinstance(scope, dict):
        findings.error(f"{model_path}: scope はオブジェクトである必要があります")
    else:
        for field in ("root", "included", "excluded", "revision"):
            if field not in scope:
                findings.error(f"{model_path}: scope.{field} がありません")

    nodes = data.get("nodes")
    edges = data.get("edges")
    zones = data.get("zones")
    unresolved = data.get("unresolved")

    if not isinstance(nodes, list) or not nodes:
        findings.error(f"{model_path}: nodes は空でない配列である必要があります")
        nodes = []
    if not isinstance(edges, list):
        findings.error(f"{model_path}: edges は配列である必要があります")
        edges = []
    if not isinstance(zones, list):
        findings.error(f"{model_path}: zones は配列である必要があります")
        zones = []
    if not isinstance(unresolved, list):
        findings.error(f"{model_path}: unresolved は配列である必要があります")

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        location = f"nodes[{index}]"
        if not require_fields(node, REQUIRED_NODE_FIELDS, location, findings):
            continue

        node_id = node.get("id")
        name = node.get("name")
        role = node.get("role")
        if not is_non_empty_string(node_id):
            findings.error(f"{location}.id: 空でない文字列が必要です")
        elif node_id in node_ids:
            findings.error(f"{location}.id: ID が重複しています: {node_id}")
        else:
            node_ids.add(node_id)

        if not is_non_empty_string(name):
            findings.error(f"{location}.name: 空でない文字列が必要です")
        if not is_non_empty_string(role):
            findings.error(f"{location}.role: 役割の一文が必要です")
        elif is_non_empty_string(name) and normalized_text(role) == normalized_text(name):
            findings.error(f"{location}.role: 名前の繰り返しではなく責務を書いてください")
        elif len(role) > 160:
            findings.warn(f"{location}.role: 160 文字を超えています。簡潔化を検討してください")

        validate_source_refs(node.get("source_refs"), location, findings)
        validate_evidence(node, location, findings)

        kind = node.get("kind")
        if schema_version == 2:
            diagram_refs = node.get("diagram_refs")
            if not isinstance(diagram_refs, list) or not diagram_refs:
                findings.error(
                    f"{location}.diagram_refs: 空でない配列が必要です"
                )
            else:
                has_required_detail = kind not in {"class", "method", "function"}
                for ref_index, diagram_ref in enumerate(diagram_refs):
                    ref_location = f"{location}.diagram_refs[{ref_index}]"
                    if not is_non_empty_string(diagram_ref):
                        findings.error(f"{ref_location}: 空でない文字列が必要です")
                        continue

                    ref_path = Path(diagram_ref)
                    if ref_path.is_absolute() or ".." in ref_path.parts:
                        findings.error(
                            f"{ref_location}: 成果物ルート相対パスを使用してください"
                        )
                        continue
                    normalized_ref = ref_path.as_posix()
                    referenced_text = diagram_texts.get(normalized_ref)
                    if referenced_text is None:
                        findings.error(
                            f"{ref_location}: 図ファイルがありません: {diagram_ref}"
                        )
                        continue

                    if is_non_empty_string(node_id):
                        id_re = re.compile(
                            rf"(?<![A-Za-z0-9_-]){re.escape(node_id)}"
                            rf"(?![A-Za-z0-9_-])"
                        )
                        if not id_re.search(referenced_text):
                            findings.error(
                                f"{ref_location}: 図にノード ID {node_id} がありません"
                            )

                    if kind == "class" and ref_path.parts[:1] == ("classes",):
                        if ref_path.name != "00-class-index.puml":
                            has_required_detail = True
                    if kind in {"method", "function"}:
                        if ref_path.parts[:1] == ("methods",):
                            if ref_path.name != "00-method-index.puml":
                                has_required_detail = True

                if not has_required_detail:
                    if kind == "class":
                        findings.error(
                            f"{location}.diagram_refs: classes/ の詳細図が必要です"
                        )
                    elif kind in {"method", "function"}:
                        findings.error(
                            f"{location}.diagram_refs: methods/ の詳細図が必要です"
                        )

        if kind in {"function", "method"}:
            for field in ("inputs", "outputs", "side_effects"):
                if field not in node:
                    findings.warn(f"{location}.{field}: 確認できる場合は追加してください")
        if kind == "api":
            for field in ("operation", "auth"):
                if field not in node:
                    findings.warn(f"{location}.{field}: 確認できる場合は追加してください")

    if schema_version == 2:
        validate_coverage(data.get("coverage"), nodes, model_path, findings)
        if "00-repository-overview.puml" not in diagram_texts:
            findings.error(
                f"{model_path}: 00-repository-overview.puml がありません"
            )

    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        location = f"edges[{index}]"
        if not require_fields(edge, REQUIRED_EDGE_FIELDS, location, findings):
            continue

        edge_id = edge.get("id")
        if not is_non_empty_string(edge_id):
            findings.error(f"{location}.id: 空でない文字列が必要です")
        elif edge_id in edge_ids:
            findings.error(f"{location}.id: ID が重複しています: {edge_id}")
        else:
            edge_ids.add(edge_id)

        if edge.get("from") not in node_ids:
            findings.error(f"{location}.from: 未定義ノードです: {edge.get('from')}")
        if edge.get("to") not in node_ids:
            findings.error(f"{location}.to: 未定義ノードです: {edge.get('to')}")
        if not is_non_empty_string(edge.get("purpose")):
            findings.error(f"{location}.purpose: 関係の目的が必要です")

        validate_source_refs(edge.get("source_refs"), location, findings)
        validate_evidence(edge, location, findings)

    validate_exchange_edges(edges, diagram_text, findings)

    zone_ids: set[str] = set()
    for index, zone in enumerate(zones):
        location = f"zones[{index}]"
        if not require_fields(zone, REQUIRED_ZONE_FIELDS, location, findings):
            continue

        zone_id = zone.get("id")
        if not is_non_empty_string(zone_id):
            findings.error(f"{location}.id: 空でない文字列が必要です")
        elif zone_id in zone_ids:
            findings.error(f"{location}.id: ID が重複しています: {zone_id}")
        else:
            zone_ids.add(zone_id)

        reasons = zone.get("boundary_reason")
        if not isinstance(reasons, list) or not reasons:
            findings.error(f"{location}.boundary_reason: 境界理由が必要です")

        validate_source_refs(zone.get("source_refs"), location, findings)
        validate_evidence(zone, location, findings)

    for node_id in sorted(node_ids):
        id_re = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(node_id)}(?![A-Za-z0-9_-])")
        if not id_re.search(catalog_text):
            findings.error(f"architecture-catalog.md: ノード ID {node_id} がありません")
        if not id_re.search(diagram_text):
            findings.error(f"PlantUML 図: ノード ID {node_id} がありません")


def resolve_local_include(source: Path, raw_value: str, root: Path) -> Path | None:
    value = raw_value.strip().strip('"').strip("'")
    if value.startswith("<") and value.endswith(">"):
        return None

    value = value.split("!", 1)[0]
    candidate = (source.parent / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return Path("/")
    return candidate


def validate_puml(
    root: Path,
    findings: Findings,
) -> tuple[list[Path], str, dict[str, str]]:
    puml_files = sorted(path for path in root.rglob("*.puml") if path.is_file())
    if not puml_files:
        findings.error(f"{root}: .puml ファイルがありません")
        return [], "", {}

    diagrams: list[Path] = []
    combined_diagrams: list[str] = []
    diagram_texts: dict[str, str] = {}
    for path in puml_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            findings.error(f"{path}: 読み取れません: {exc}")
            continue

        relative = path.relative_to(root)
        if SECRET_RE.search(text):
            findings.error(f"{relative}: 秘密値らしい文字列が含まれています")
        if REMOTE_IMAGE_RE.search(text):
            findings.error(f"{relative}: リモート画像参照は禁止です")

        for match in INCLUDE_RE.finditer(text):
            directive = match.group(1).casefold()
            raw_value = match.group(2)
            if directive == "includeurl" or REMOTE_RE.search(raw_value):
                findings.error(f"{relative}: リモート include は禁止です: {raw_value}")
                continue

            included = resolve_local_include(path, raw_value, root)
            if included == Path("/"):
                findings.error(f"{relative}: 成果物ディレクトリ外を参照しています: {raw_value}")
            elif included is None:
                findings.warn(f"{relative}: PlantUML 標準ライブラリ依存を確認してください: {raw_value}")
            elif not included.is_file():
                findings.error(f"{relative}: include 先がありません: {raw_value}")

        is_shared = "_shared" in relative.parts
        if is_shared:
            continue

        starts = len(START_RE.findall(text))
        ends = len(END_RE.findall(text))
        if starts == 0:
            findings.error(f"{relative}: @start... がありません")
            continue
        if starts != ends:
            findings.error(f"{relative}: 開始タグ {starts} 個と終了タグ {ends} 個が一致しません")
        if not TITLE_RE.search(text):
            findings.error(f"{relative}: title がありません")
        if SEQUENCE_PARTICIPANT_RE.search(text) and not AUTONUMBER_RE.search(text):
            findings.error(f"{relative}: シーケンス図には autonumber が必要です")

        if ("<<inferred>>" in text or "<<trust-boundary>>" in text) and not LEGEND_RE.search(text):
            findings.warn(f"{relative}: 推測または信頼境界の記号に凡例がありません")

        long_lines = [number for number, line in enumerate(text.splitlines(), 1) if len(line) > 180]
        if long_lines:
            preview = ", ".join(str(number) for number in long_lines[:5])
            findings.warn(f"{relative}: 180 文字を超える行があります: {preview}")

        diagrams.append(path)
        combined_diagrams.append(text)
        diagram_texts[relative.as_posix()] = text

    if not diagrams:
        findings.error(f"{root}: 描画対象の PlantUML 図がありません")
    return diagrams, "\n".join(combined_diagrams), diagram_texts


def plantuml_base_command(
    root: Path,
    plantuml_jar: Path | None,
) -> tuple[list[str] | None, dict[str, str]]:
    environment = os.environ.copy()
    environment["PLANTUML_SECURITY_PROFILE"] = "ALLOWLIST"
    environment["plantuml.allowlist.path"] = str(root)
    environment["plantuml.include.path"] = str(root)

    if plantuml_jar is not None:
        java = shutil.which("java")
        if java is None or not plantuml_jar.is_file():
            return None, environment
        return (
            [
                java,
                "-DPLANTUML_SECURITY_PROFILE=ALLOWLIST",
                f"-Dplantuml.allowlist.path={root}",
                f"-Dplantuml.include.path={root}",
                "-jar",
                str(plantuml_jar),
            ],
            environment,
        )

    executable = shutil.which("plantuml")
    if executable is None:
        return None, environment
    return [executable], environment


def run_plantuml(
    diagrams: list[Path],
    root: Path,
    plantuml_jar: Path | None,
    render_svg: bool,
    require_plantuml: bool,
    findings: Findings,
) -> None:
    command, environment = plantuml_base_command(root, plantuml_jar)
    if command is None:
        message = "PlantUML が見つからないため構文検査とレンダリングは未実施です"
        if require_plantuml:
            findings.error(message)
        else:
            findings.warn(message)
        return

    check_command = [*command, "--check-syntax", *(str(path) for path in diagrams)]
    result = subprocess.run(
        check_command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stdout + result.stderr).strip()
        findings.error(
            "PlantUML 構文検査に失敗しました"
            + (f": {details[-3000:]}" if details else "")
        )
        return

    if not render_svg:
        return

    output_directory = root / "rendered"
    output_directory.mkdir(parents=True, exist_ok=True)
    for diagram in diagrams:
        relative = diagram.relative_to(root)
        diagram_output = output_directory / relative.parent
        diagram_output.mkdir(parents=True, exist_ok=True)
        render_command = [
            *command,
            "--format",
            "svg",
            "--output-dir",
            str(diagram_output),
            str(diagram),
        ]
        render_result = subprocess.run(
            render_command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if render_result.returncode != 0:
            details = (render_result.stdout + render_result.stderr).strip()
            findings.error(
                f"PlantUML SVG レンダリングに失敗しました: {relative}"
                + (f": {details[-3000:]}" if details else "")
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PlantUML アーキテクチャ成果物を検査します。"
    )
    parser.add_argument("target", type=Path, help="成果物ディレクトリ")
    parser.add_argument("--plantuml-jar", type=Path, help="PlantUML JAR のパス")
    parser.add_argument(
        "--skip-plantuml",
        action="store_true",
        help="PlantUML の構文検査を省略する",
    )
    parser.add_argument(
        "--require-plantuml",
        action="store_true",
        help="PlantUML が見つからない場合もエラーにする",
    )
    parser.add_argument(
        "--render-svg",
        action="store_true",
        help="構文検査後に rendered/ へ SVG を生成する",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.target.resolve()
    findings = Findings()

    if not root.is_dir():
        print(f"ERROR: 成果物ディレクトリがありません: {root}", file=sys.stderr)
        return 2

    catalog_path = root / "architecture-catalog.md"
    model_path = root / "architecture-index.json"

    if catalog_path.is_file():
        catalog_text = catalog_path.read_text(encoding="utf-8")
    else:
        findings.error(f"{catalog_path}: 必須カタログがありません")
        catalog_text = ""

    diagrams, diagram_text, diagram_texts = validate_puml(root, findings)

    if model_path.is_file():
        validate_model(
            model_path,
            catalog_text,
            diagram_text,
            diagram_texts,
            findings,
        )
    else:
        findings.error(f"{model_path}: 必須モデルがありません")

    for path in (catalog_path, model_path):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if SECRET_RE.search(text):
                findings.error(f"{path}: 秘密値らしい文字列が含まれています")

    if not args.skip_plantuml and diagrams:
        plantuml_jar = (
            args.plantuml_jar.resolve() if args.plantuml_jar is not None else None
        )
        run_plantuml(
            diagrams,
            root,
            plantuml_jar,
            args.render_svg,
            args.require_plantuml,
            findings,
        )

    for warning in findings.warnings:
        print(f"WARN: {warning}")
    for error in findings.errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if findings.errors:
        print(
            f"検証失敗: {len(findings.errors)} error(s), {len(findings.warnings)} warning(s)",
            file=sys.stderr,
        )
        return 1

    print(f"検証成功: 0 error(s), {len(findings.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
