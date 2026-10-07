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

R04-b/v2 增加单独的固定前缀初始化入口。它不挂载游戏，使用空的私有 Steam
客户端目录，并屏蔽 AMD 可选贡献目录与两个 NVIDIA Wine DLL 路径。仅允许
已登记的 `getcompatpath /work`、`cmd.exe` 常量写入/回读和私有 wineserver 等待。
原始清单、许可、保护前像和固定命令均须登记；新鲜 Judge 与 Governor 批准
精确候选后才允许 `execute`。改变候选或复用已初始化前缀会拒绝执行。

```sh
python3 -B -m src.testing.bootstrap prepare
# 使用 prepare 返回的绝对路径；dry 只运行隔离探针。
python3 -B -m src.testing.bootstrap dry --profile /absolute/private/profile
python3 -B -m unittest discover -s tests -p 'test_bootstrap.py' -v
```

Wine 的 `Z:` 指向隔离命名空间根目录。主机侧清单使用不跟随链接的遍历，
不能在主机上解析前缀链接。前缀/CLI 证据不表示 Steam、游戏、存档、云同步、
加载器或任何 MOD 组合已验收。

R04-c 的 `runtime_probe` 只读解析 PE32/PE32+ 普通与延迟导入，不加载任何
DLL，不启动应用。损坏、未终止、无法映射或读取期间改变的文件会拒绝输出
依赖结论。导入表和静态文件位置不能证明 DLL 搜索顺序或运行时兼容；
API-set 名称也不能仅凭缺少同名文件判定缺库。

```sh
python3 -B -m src.testing.runtime_probe /absolute/referenced/image.exe
python3 -B -m unittest discover -s tests -p 'test_runtime_probe.py' -v
```

R04-d 的 `gpu_probe` 是独立、自有的原生离屏 Vulkan 实验，使用现有 gcc
和系统 Vulkan 开发库。每次创建新的私有目录，只暴露三个固定 NVIDIA
设备与只读系统/硬件视图；不挂载 Steam、游戏、Proton、显示、输入或音频。
先实测私有命名空间边界，再执行一次 32×32 清屏、GPU 复制和精确回读。
GPU 等待上限 2 秒，原生子进程 10 秒，整个命名空间 20 秒；超时只结束
本次拥有的进程。驱动可能在私有 XDG 缓存中生成 GLCache 文件。

```sh
python3 -B -m src.testing.gpu_probe
```

结果及原始挂载、设备、源码/二进制身份保存在忽略的 `.local/r04/gpu/`。
Root 另核对精确回读、PCI 身份、所有输出和保护前像。该证据只证明原生
Vulkan 离屏清屏/复制可用；Windows/D3D12、窗口呈现、中文字体、Steam、
存档/云同步、加载器和 MOD 兼容仍需各自的真实验证。
