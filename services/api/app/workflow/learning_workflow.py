from __future__ import annotations

import os
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.services.analysis.postprocess.repair_policy import (
    should_trigger_patch_repair,
    should_trigger_repair,
)
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


def _resolve_repair_mode(state: AnalyzeState) -> str:
    """解析 repair mode：state 优先（来自 config），fallback 到 env，默认 patch。

    优先级：state["repair_mode"] > env CLAREAD_WORKFLOW_REPAIR_MODE > "patch"。
    state["repair_mode"] 由 derive_user_config_node 从 config 写入，
    确保显式 config 在条件路由阶段也能生效。
    非法 env 值 fail-safe 到 "full_result"。
    """
    mode = state.get("repair_mode")
    if mode in ("full_result", "patch"):
        return mode
    # Fallback to env
    env_val = os.environ.get("CLAREAD_WORKFLOW_REPAIR_MODE")
    if env_val == "patch":
        return "patch"
    if env_val == "full_result":
        return "full_result"
    if env_val is not None:
        # Invalid env value → fail-safe to full_result
        return "full_result"
    # Default
    return "patch"


def _should_repair(state: AnalyzeState) -> bool:
    """判断是否需要触发 repair_agent。

    full_result mode：只看 drop_log + 旧 annotations（旧逻辑不变）。
    patch mode：合并 drop_log + canonical_drop_log，用 normalized_annotations 计数。
    repair_mode 从 state 读取（由 derive_user_config_node 从 config 写入），
    fallback 到 env CLAREAD_WORKFLOW_REPAIR_MODE。
    """
    normalized_result = state.get("normalized_result")
    mode = _resolve_repair_mode(state)

    if mode == "patch":
        return should_trigger_patch_repair(
            normalized_result, threshold=0.35
        )

    return should_trigger_repair(normalized_result, threshold=0.35)


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
