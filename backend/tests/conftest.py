"""全局测试配置：pytest 先于任何测试模块加载本文件。

- VACLEAD_DISABLE_JOBS=1：禁用定时任务注册与 G4 启动补跑。测试库一旦有企业数据，
  后续模块的 TestClient lifespan 会对着真网络后台跑每日增量（持写事务跨 HTTP 调用），
  与用例并发写 SQLite → database is locked → 导入端点随机 500
  （SPEC-I I0c 实证：agents 模块建档 → api 模块 lifespan 点燃补跑）。
- 各测试模块仍各自 setdefault 自己的 VACLEAD_DB 临时库（本文件不代设，
  避免改变"首个导入模块定库"的既有语义）。
"""
import os

os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")
