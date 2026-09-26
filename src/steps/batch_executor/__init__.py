# -*- coding: utf-8 -*-
"""批量执行器包。

由 batch_executor.py 拆分为包结构，本模块保持原有导入接口不变：
    from .batch_executor import BatchExecutor
"""
from .executor import BatchExecutor

__all__ = ["BatchExecutor"]
