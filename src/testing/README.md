# 隔离测试准备

R04-a/v1 只构建空的私有前缀/配置目录，并运行固定的 Python 隔离探针。
此入口不能启动 Proton、Steam、游戏或任意应用。源码路径通过只读挂载引用，
不复制 MOD、游戏资产、真实前缀、存档或 Steam 会话。

私有上下文、目录清单、精确挂载和原始结果保存在 `.local/r04/`。
上下文包含 `readonly_sources` 的 `game` / `proton` 原目录与 `protected_roots`。
目录必须分离；既有目标、符号链接、共享 inode、归属不符和缺少 bubblewrap
均拒绝执行，不提供非隔离回退。创建目录为一次性 UUID，只写自身 `work` 子树。

```sh
python3 -B -m src.testing.isolation --context .local/r04/context.json
python3 -B -m unittest discover -s tests -p 'test_isolation.py' -v
```

探针验证只读 canary、路径逃逸、网络与 IPC 命名空间、环境、设备和真实源只读挂载。
该证据不代表真实游戏、存档/云同步、加载器或组合兼容性已通过；这些仍为 `NOT_RUN`。
启动任何运行时需要后续契约与相应证据。
