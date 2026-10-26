# 游戏内运行时模块

这里保存本仓库自有的 Lua 模块，不包含第三方加载器或游戏资产。旧管理应用的路线仍按项目状态门禁执行；本目录的测试模块用于当前已授权 MOD 验证。

`NativeOutfitOwnership.lua` 观察原生装备菜单的实际换装事件，只清除 CNS 保存数据中两个 Body 覆盖标记。原生初始化、其他身体部位与预设保持原有归属。当前通过范围见 `PROJECT_STATE.json` 的分组测试结果。

`QAControl.lua` 提供本次私有会话的 `observe`、`move`、`stop`，使位置、速度、输入锁与短时移动检查可以使用结构化读数。外观、材质和动作自然度仍需真实画面检查。这个入口验证语义移动，不能代替物理键盘、手柄或窗口焦点测试。

由本地启动文件调用：

```lua
require("QAControl").start({directory="<私有会话目录>", nonce="<新的会话标识>"})
```

目录必须新建且可写；Windows/Proton 使用其实际可访问的路径。启动、游戏进程和输入所有权由调用者先核实。会话标识与原始响应保留在忽略目录，不能提交真实本机配置。每次正常重启使用新目录和新标识。

原子写入 `request.txt`，仅一行九个制表符分隔字段：

```text
nonce	id	expires_unix	op	duration_ms	world_dx	world_dy	observed_pawn	observed_world
```

先 `observe`，然后将返回的 Pawn/World 身份传入 `move`。时长为 20–800 毫秒，20 毫秒的整数倍；方向仅世界坐标水平四个基轴。请求截止时间为当前时间至五秒内。每个新 ID 只执行一次，返回 `<id>.active.tsv` 和 `<id>.response.tsv`；超时后必须先查状态和已有回执，不能盲目重发。

所有 UObject 调用在游戏线程运行。移动只向原生输入累加器提交 `bForce=false` 的输入；原生锁定、非步行模式、Pawn/World 变化会停止提交。完成后读取自然减速，不改速度、位置、锁或存档。`stop` 仅停止后续提交，不能撤销已经发生的移动。模块有二十分钟会话期限和二百请求上限。加载它需要正常退出本任务的游戏并添加独立模块；不要开启整套 MOD 热重载。

离线行为验证：

```sh
python3 -m unittest discover -s tests -p test_native_outfit_ownership.py -v
python3 -m unittest discover -s tests -p test_qa_control.py -v
```

真实 UE4SS 绑定、运行结果与未覆盖项以唯一项目状态引用的本地证据为准。REA 的静态二进制检查与 UE 运行时反射分别记录，不把文件格式或签名发现称为可调用性验证。
