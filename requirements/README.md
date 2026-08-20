# 依赖边界 / Dependency boundary

`requirements-data-action-demo.txt` 固定本次验收使用的直接 Python 依赖版本；CUDA 版 PyTorch
必须通过 README 中指定的 PyTorch CUDA 12.6 或 CPU 索引单独安装。`requirements-neo4j-optional.txt`
只用于尚未验收的 Neo4j 可选分支。

These files pin the validated direct dependencies, not every transitive wheel hash. They therefore
support repeatable version selection but do not claim byte-for-byte reproducibility across package
indexes. The exact acceptance environment is recorded in
`docs/acceptance/acceptance_report_v0001.json`.
