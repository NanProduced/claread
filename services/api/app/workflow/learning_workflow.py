from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.workflow.analyze_nodes import (
    assemble_result_node,
    derive_user_config_node,
    normalize_and_ground_node,
    parallel_agents_node,
    prepare_input_node,
    project_render_scene_node,
    repair_agent_node,
)
from app.workflow.analyze_state import AnalyzeState


def _should_repair(state: AnalyzeState) -> bool:
    """判断是否需要触发 repair_agent。

    只统计 quality drops（排除 density_control 正常裁剪），
    与 repair_agent_node 内部的判断标准保持一致。
    """
    normalized_result = state.get("normalized_result")
    if normalized_result is None:
        return False

    drop_log = normalized_result.drop_log or []
    quality_drops = [d for d in drop_log if d.drop_stage != "density_control"]
    quality_drop_count = len(quality_drops)
    annotation_count = len(normalized_result.annotations)

    if annotation_count == 0:
        return False

    failure_ratio = quality_drop_count / (annotation_count + quality_drop_count)
    return failure_ratio > 0.35


def build_learning_graph() -> Any:
    graph = StateGraph(AnalyzeState)

    # 基础节点
    graph.add_node("prepare_input", prepare_input_node)
    graph.add_node("derive_user_config", derive_user_config_node)

    # 并行 agent 节点（单一入口，避免重复调用）
    graph.add_node("parallel_agents", parallel_agents_node)

    # 归一化节点
    graph.add_node("normalize_and_ground", normalize_and_ground_node)

    # 可选 repair 节点
    graph.add_node("repair_agent", repair_agent_node)

    # 投影和结果收敛
    graph.add_node("project_render_scene", project_render_scene_node)
    graph.add_node("assemble_result", assemble_result_node)

    # 边连接
    graph.add_edge(START, "prepare_input")
    graph.add_edge("prepare_input", "derive_user_config")

    # 并行 agent 执行（在 derive_user_config 之后，单一入口）
    graph.add_edge("derive_user_config", "parallel_agents")

    # 归一化（在并行 agent 完成之后）
    graph.add_edge("parallel_agents", "normalize_and_ground")

    # Repair（条件触发）
    graph.add_conditional_edges(
        "normalize_and_ground",
        _should_repair,
        {
            True: "repair_agent",
            False: "project_render_scene",
        },
    )

    # Repair 之后继续投影
    graph.add_edge("repair_agent", "project_render_scene")

    # 最终结果收敛
    graph.add_edge("project_render_scene", "assemble_result")
    graph.add_edge("assemble_result", END)

    return graph.compile()
