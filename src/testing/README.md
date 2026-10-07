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

R04-e 的 `d3d12_probe` 用现有 GCC/binutils 编译自有、无 CRT 的固定 64 位
Windows 控制台探针。GNU ld 直接把 ELF 目标链接为 PE；临时自有 DLL 仅用于
生成 KERNEL32 导入库，构建后删除，不进入运行环境。独立 PE 解析器校验
固定导入、NX、动态基址和控制台类型。没有可配置程序、命令、前缀或环境入口。

```sh
python3 -B -m src.testing.d3d12_probe
python3 -B -m unittest discover -s tests -p 'test_d3d12_probe.py' -v
```

每次使用新私有前缀和上述三个 GPU 设备，不复用 R04-b 的启动批准。
冻结 Proton 原目录只读，空客户端、私有 home/cache/run/shm、网络与 PID
隔离在启动前实测。固定 DLL 选择只作用于本次命名空间。前缀准备 40 秒，
Windows 探针 20 秒，wineserver 等待 15 秒，整个命名空间 90 秒；GPU fence
最多等待 2 秒。超时结束本次拥有的进程组，最终销毁整个私有 PID 命名空间。

已实测：首个 Windows 探针正常退出并报告 DXGI `0x887a0004`；核对冻结
Proton 的 `runinprefix` 跳过前缀更新后，固定选择原有 DXVK/vkd3d DLL 在
新前缀复测。DXVK 初始化有日志，但 Windows 步骤超过 20 秒。两次启动前
27 项边界检查和 Root 的原始文件/存档保护核对通过；第二次超时后内部检查
未运行，主机侧保护与命名空间父进程回收另已核实。随后进程组超时修复通过
真实继承管道子进程的回归检查；修复后的启动器未再次运行 Windows 探针。

因此 D3D12 设备、清屏、复制、精确回读均未验收，继续使用原始失败记录。
原始前缀、日志、输入身份与输出清单位于 `.local/r04/d3d12/`，公开脱敏事实
位于 `docs/evidence/R04-e.json`。不把文件存在或原生 Vulkan 成功当作 Windows
图形证据，也不改变真实游戏、Steam、账户、Cloud、存档或 MOD 配置。
