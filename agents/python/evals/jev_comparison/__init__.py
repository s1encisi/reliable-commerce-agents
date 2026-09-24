"""Jev（TypeSafe System One）对比运行框架。

把两个决策点与团队在没有决策模型时会交付的基于规则的实现做对比测量：

    routing  (choice)  vs  加权关键词匹配
    gate     (noul)    vs  正则拒绝名单

从 ``agents/python`` 运行::

    TYPESAFE_API_KEY=... python -m evals.jev_comparison.runner

输出参见 ``results/``，集成说明以及"本方案不主张什么"的清单参见
``../../shared/jev/README.md``。
"""
