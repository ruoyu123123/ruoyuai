#!/usr/bin/env python3
"""log_util.py — 统一日志接口（2026-06-17）

四级日志标准：
- SILENT: orchestrator 默认，只输出结果 JSON（程序驱动模式）
- INFO: GUI 进度条用，关键 milestone（如 "✓ 第 3 步完成"）
- DEBUG: 开发调试，需 RUOYU_DEBUG=1 环境变量
- ERROR: 异常，走 stderr（Traceback 自动捕获）

用法：
    from log_util import get_logger
    logger = get_logger(__name__)
    logger.info("关键进度")
    logger.debug("调试信息")
    logger.error("错误信息")
"""
import os
import sys
import logging
from typing import Literal

LogLevel = Literal["SILENT", "INFO", "DEBUG", "ERROR"]

# 全局开关：orchestrator 驱动时设为 SILENT，开发调试时用 DEBUG
_DEFAULT_LEVEL = os.environ.get("RUOYU_LOG_LEVEL", "INFO").upper()
_DEBUG_MODE = os.environ.get("RUOYU_DEBUG", "0") == "1"


def get_logger(name: str, level: LogLevel | None = None) -> logging.Logger:
    """获取统一配置的 logger。

    Args:
        name: 模块名（通常传 __name__）
        level: 日志级别，None 则用环境变量 RUOYU_LOG_LEVEL 或默认 INFO

    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger(name)

    # 避免重复配置（同一 name 多次调用）
    if logger.handlers:
        return logger

    # 确定实际级别
    if level is None:
        level = _DEFAULT_LEVEL

    if level == "SILENT":
        # 完全静默，只输出通过 print_result_json() 的结构化结果
        logger.setLevel(logging.CRITICAL + 1)  # 高于所有标准级别
        logger.addHandler(logging.NullHandler())
    elif level == "DEBUG" or _DEBUG_MODE:
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
            datefmt="%H:%M:%S"
        ))
        logger.addHandler(handler)
    elif level == "INFO":
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "[%(levelname)s] %(message)s"
        ))
        logger.addHandler(handler)
    elif level == "ERROR":
        logger.setLevel(logging.ERROR)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "[ERROR] %(name)s:%(lineno)d - %(message)s"
        ))
        logger.addHandler(handler)

    # 阻止日志向上传播（避免与 root logger 重复）
    logger.propagate = False

    return logger


def print_result_json(data: dict, **kwargs):
    """打印结构化结果（即使 SILENT 模式也输出，供 orchestrator/GUI 解析）。

    Args:
        data: 要输出的字典（自动 JSON 序列化）
        **kwargs: 传给 json.dumps 的额外参数
    """
    import json
    print(json.dumps(data, ensure_ascii=False, **kwargs))


def print_progress(step: int, total: int, message: str):
    """打印进度信息（GUI 友好格式）。

    Args:
        step: 当前步骤号
        total: 总步骤数
        message: 进度描述
    """
    # INFO 级别才输出（SILENT 模式静默）
    if _DEFAULT_LEVEL != "SILENT":
        print(f"[{step}/{total}] {message}", flush=True)


# 便捷函数：快速打印而不创建 logger 实例
def info(message: str):
    """快速打印 INFO 级别消息"""
    if _DEFAULT_LEVEL in ("INFO", "DEBUG"):
        print(f"[INFO] {message}", flush=True)


def debug(message: str):
    """快速打印 DEBUG 级别消息（需 RUOYU_DEBUG=1）"""
    if _DEBUG_MODE or _DEFAULT_LEVEL == "DEBUG":
        print(f"[DEBUG] {message}", flush=True)


def error(message: str):
    """快速打印 ERROR 级别消息（走 stderr）"""
    print(f"[ERROR] {message}", file=sys.stderr, flush=True)


def warning(message: str):
    """快速打印 WARNING 级别消息"""
    if _DEFAULT_LEVEL != "SILENT":
        print(f"[WARNING] {message}", flush=True)


# 测试用例
if __name__ == "__main__":
    print("=== 测试 log_util ===")

    # 测试不同级别
    for level in ["DEBUG", "INFO", "SILENT"]:
        print(f"\n--- {level} 模式 ---")
        os.environ["RUOYU_LOG_LEVEL"] = level
        logger = get_logger(f"test_{level.lower()}", level=level)
        logger.debug("这是调试信息")
        logger.info("这是关键进度")
        logger.warning("这是警告")
        logger.error("这是错误")

    # 测试便捷函数
    print("\n--- 便捷函数 ---")
    os.environ["RUOYU_LOG_LEVEL"] = "INFO"
    info("便捷 INFO")
    debug("便捷 DEBUG（需 RUOYU_DEBUG=1）")
    warning("便捷 WARNING")
    error("便捷 ERROR")

    # 测试结构化输出
    print("\n--- 结构化输出 ---")
    print_result_json({"status": "success", "data": {"count": 42}}, indent=2)

    print("\n--- 进度输出 ---")
    print_progress(1, 5, "第一步")
    print_progress(2, 5, "第二步")
