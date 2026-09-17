# -*- coding: utf-8 -*-
"""宁波鲍斯能源装备 · 智能问数平台（灵犀 Lingxi）（数据层）。

本包提供数据层的三大能力：
- config.py           全局配置（路径 / 随机种子 / 时间范围）
- schema.py           6 张表的 schema 定义（单一数据源，含 DDL 与中文元数据）
- data_generator.py   可复现的数据生成逻辑
- warehouse.py        DuckDB 数仓建库、灌数与校验
"""
