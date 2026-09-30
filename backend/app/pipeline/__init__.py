"""流水线包：可重复的初始化 / 构建 / 部署流水线。

阶段固定为：
    preflight -> migrate -> generate -> load -> reconcile -> verify -> publish

每个阶段都写检查点，成功后重跑直接跳过；失败阶段不允许把批次标记为就绪。
"""
