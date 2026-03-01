# PebbleOS Toward a Linux-Like, Pebble-Native System

## Summary

目标是把 PebbleOS 从“Pebble 管理可见系统行为、Python 托底执行与设备桥接”的状态，推进到“Pebble 定义系统 ABI、任务模型、文件系统语义、服务管理与用户空间工具；Python 只保留最小宿主 VM/终端/时钟/持久化桥”的结构。

基于当前仓库现状，PebbleOS 已经有这些基础：

- Pebble 侧 shell 和 runtime：[`/Users/xulixin/LX_OS/pebble_system/shell.peb`](/Users/xulixin/LX_OS/pebble_system/shell.peb)
- Pebble 侧 VFS/MFS、路径模型、内存/heap：[`/Users/xulixin/LX_OS/pebble_system/runtime.peb`](/Users/xulixin/LX_OS/pebble_system/runtime.peb)
- 前后台任务、VM task snapshot、`ps/jobs/fg` 雏形
- Python bootloader/host ABI 仍然承担解释器、bytecode VM、终端 I/O、原始文件系统和后台线程调度：[`/Users/xulixin/LX_OS/pebble_bootloader/shell.py`](/Users/xulixin/LX_OS/pebble_bootloader/shell.py)

和现代 Linux 风格系统相比，当前主要欠缺：

- 稳定的 syscall/ABI 边界
- 真正系统化的进程模型、wait/signal/session/process group
- 可组合的 stdin/stdout/stderr、pipe、重定向
- 服务管理与 init/system daemon
- 权限/用户/组/能力模型
- 更完整的文件系统元数据、inode-like identity、mount/table、durability 语义
- 套接字/网络栈抽象
- 包管理、版本化 ABI、安装布局
- 构建链与自举链，Pebble 还不能自己编译/检查/生成 Pebble 代码
- 观测、日志、故障恢复、兼容策略

你已经选定的策略：

- 第一优先：`Pebble-native core`
- 兼容策略：`Allow breaking changes`

这意味着路线要优先做“Pebble 自己定义和驱动系统”，而不是先把 shell 命令补全成 Linux 外观。

## Architecture Direction

最终目标架构分四层：

1. Host substrate
只保留 Python 中无法在当前阶段移除的最小宿主能力：
- Pebble source/bytecode 执行引擎
- 终端原始输入输出
- 宿主持久化读写
- 宿主时钟
- 宿主线程/事件循环载体

2. Pebble kernel/runtime layer
放到 `system/runtime.peb` 及后续 `system/kernel/*.peb`：
- syscall ABI 封装
- task/process table
- scheduler policy
- VFS policy
- fd table
- signal/event abstraction
- service manager
- capability/security model

3. Pebble base system
放到 `system/bin/*.peb`、`system/lib/*.peb`：
- init
- sh
- coreutils
- ps/top-like tooling
- package/service tooling
- logs/config tools

4. Userland/apps
普通 Pebble 程序只依赖稳定 ABI，不直接碰 host bridge。

核心原则：

- 禁止在新功能里直接新增面向 shell 的 Python 特例入口
- 所有新系统能力先定义 Pebble 侧 ABI，再决定是否需要 Python bridge 实现
- Python 只暴露“原子宿主原语”，不暴露高层策略
- 尽快把当前分散的 host functions 收敛成一套 syscall 表

## Public APIs / Interface Changes

第一阶段开始就要明确这些接口调整。

### 1. Host function interface 重构为 syscall ABI

现状是 `PebbleInterpreter(... host_functions={...})` 直接注入大量高层函数，如：
- `create_file`
- `run_program`
- `start_background_job`
- `term_read_key`
- `current_time`

需要改成分层接口：

- Pebble 可见高层 API：
  - `syscall(name, args)`
  - 或模块化形式：`sys.open`, `sys.spawn`, `sys.read`, `sys.write`, `sys.clock`, `sys.term`

- Python 仅实现低层 syscall handler：
  - `host_open`
  - `host_read`
  - `host_write`
  - `host_spawn_vm`
  - `host_poll_event`
  - `host_time_now`
  - `host_persist_commit`

不再让 Pebble shell/runtime 直接感知 Python 中的“背景 job”“shell current cwd”之类高层概念。

### 2. Task model 从 job/thread 升级为 process/fd/session model

新增 Pebble 侧统一对象：

- `process`
  - `pid`
  - `ppid`
  - `pgid`
  - `sid`
  - `state`
  - `cwd`
  - `argv`
  - `env`
  - `fds`
  - `exit_status`
  - `signal_mask`
- `fd`
  - `fd number`
  - `kind` (`file`, `pipe`, `tty`, `service`, `socket`)
  - `flags`
  - `offset`
- `mount`
  - `source`
  - `target`
  - `fstype`
  - `flags`

当前 `BackgroundJob` 和 `VMTask` 需要被视为过渡实现，最终删除。

### 3. Shell command surface 迁移

当前 shell 还是固定 dispatch：
[`/Users/xulixin/LX_OS/pebble_system/shell.peb`](/Users/xulixin/LX_OS/pebble_system/shell.peb)

目标改成：
- 内建只保留 `cd`, `exit`, `export`, `set`, `jobs`, `fg`, `bg`, `source`
- 其它命令统一走 `/system/bin/<cmd>.peb`
- 支持 PATH 搜索
- 支持 exit code
- 支持 pipeline 和 redirection AST

### 4. Filesystem interface 增强

当前是路径级 helper：
- `file_exists`
- `read_file`
- `write_file`
- `make_directory`

目标接口：
- `open(path, flags, mode) -> fd`
- `read(fd, size)`
- `write(fd, data)`
- `seek(fd, off, whence)`
- `close(fd)`
- `stat(path|fd)`
- `readdir(path|fd)`
- `mkdir`, `unlink`, `rename`, `mount`, `umount`

这样后续 pipe、socket、device、procfs 都能走同一模型。

## Phased Plan

## Phase 0: Establish Truth and Freeze the Boundary

目标：把“哪些是 Python 宿主原语，哪些是 Pebble 系统策略”彻底画清。

实施内容：

1. 盘点并分类当前 `host_functions`
分类为：
- terminal primitives
- filesystem raw primitives
- clock/time
- program execution
- scheduler/task control
- shell/session state

2. 制定 syscall 编号/名称表
至少包含：
- fs
- proc
- term
- clock
- memory
- service
- net 预留号段

3. 明确禁止新增高层 Python shell helper
所有后续功能先落 Pebble 侧 runtime API 设计，再决定是否加 Python primitive。

4. 产出 ABI 文档
新增文档，定义：
- syscall 名称
- 参数/返回格式
- 错误码约定
- 阻塞/非阻塞语义
- 哪些语义由 Pebble 决定，哪些由 host 保证

完成标准：

- 可以把当前 `shell.py` 中 host functions 一一映射到 syscall 分类
- 新功能开发不再直接讨论 “加一个 Python helper”，而是先讨论 Pebble ABI

## Phase 1: Pebble Kernel Split and Stable Runtime ABI

目标：把 `system/runtime.peb` 从“大杂烩 runtime”拆成内核化结构。

实施内容：

1. 拆分 `system/runtime.peb`
拆为：
- `system/kernel/syscall.peb`
- `system/kernel/fs.peb`
- `system/kernel/proc.peb`
- `system/kernel/sched.peb`
- `system/kernel/term.peb`
- `system/lib/base.peb`

2. 在 Pebble 中建立统一错误模型
- `errno` 风格整数错误码
- `result` record 约定，或异常转错误码边界
- 所有 shell 命令都能拿到退出状态

3. 把当前 runtime 全局状态结构化
例如：
- `RUNTIME_SCHEDULER`
- cwd
- FS mode
- snapshots
都迁移到明确的 kernel state 对象

4. 引入环境变量与进程上下文
当前只有 `ARGV/ARGC/CWD/FS_MODE`
需要补：
- `ENV`
- `PID`
- `PPID`
- `UID/GID` 先保留占位
- `UMASK`
- `PATH`

完成标准：

- shell 和普通程序只依赖稳定 runtime/kernel API
- 后续 fs/proc/service 工程不再往旧 runtime blob 里堆函数

## Phase 2: Real Process Model Before More Commands

目标：先把“作业”做成“进程”，再补 Linux 风格用户空间。

实施内容：

1. 统一当前 `VMTask` 和 `BackgroundJob`
Pebble 侧定义单一 process table。
Python 侧仅提供两类底层执行载体：
- VM-backed process
- host-assisted blocking worker

2. 引入 process lifecycle
必须具备：
- `spawn`
- `exec`
- `fork-like clone` 或明确暂不支持
- `waitpid`
- `exit(status)`
- zombie/reap 语义
- parent-child relationship

3. 引入 process group / session
用于：
- job control
- 前台/后台切换
- Ctrl-C / Ctrl-Z 投递
- pipeline 整组管理

4. 信号模型最小集
先做：
- `SIGINT`
- `SIGTSTP`
- `SIGTERM`
- `SIGCHLD`

5. `/proc` 风格虚拟视图
最先做 Pebble 虚拟目录：
- `/proc`
- `/proc/<pid>/status`
- `/proc/<pid>/cmdline`
- `/proc/meminfo`
- `/proc/mounts`

完成标准：

- `ps/jobs/fg` 不再直接依赖 Python 的两套 job/task 数据结构
- shell 可以 wait 一个进程并拿到 exit status
- Ctrl-C/Ctrl-Z 通过统一进程模型生效

## Phase 3: File Descriptor, Pipe, and Redirection Layer

目标：没有 fd 和 pipe，就不可能真正接近 Linux。

实施内容：

1. 引入 fd table
每个 process 拥有：
- `0 stdin`
- `1 stdout`
- `2 stderr`

2. 引入对象统一 I/O 接口
支持：
- regular file
- tty
- pipe
- procfs node
- service endpoint
- future socket

3. 支持 shell redirection
第一批：
- `>`
- `>>`
- `<`
- `2>`
- `2>&1`

4. 支持 pipeline
第一批：
- `cmd1 | cmd2`
- 前后台 pipeline job
- exit code 传播规则明确化

5. `cat`, `echo`, `grep`, `wc`, `head`, `tail` 等开始走流式 I/O
不再依赖整文件 helper。

完成标准：

- shell 能跑真实 pipeline
- 命令不必一次读完整文件
- 终端、文件、pipe 使用同一 fd 抽象

## Phase 4: Linux-Like Shell and Base Userland

目标：在内核抽象就位后，把交互面做得接近 Linux。

实施内容：

1. shell parser 重做
支持：
- quoting
- escaping
- pipelines
- redirections
- command substitution 先不做或后做
- env assignment 前缀
- builtin vs external command dispatch

2. 命令搜索与安装布局
- `/system/bin`
- `/system/sbin`
- `/bin` 兼容链接或映射
- `PATH`

3. coreutils 第一批
按优先级：
- `echo`
- `find`
- `grep`
- `wc`
- `head`
- `tail`
- `less` 或 `more`
- `chmod/chown` 先占位
- `ln`
- `mount`
- `umount`
- `env`
- `export`
- `which`
- `kill`

4. 文本配置布局
- `/etc/profile`
- `/etc/passwd` 占位
- `/etc/group` 占位
- `/etc/fstab`
- `/etc/init.d` 或 `/etc/services`

完成标准：

- 日常系统操作不再依赖 Pebble 特有交互模式
- 用户可以像 Linux 一样组合命令

## Phase 5: Init, Service Management, and Long-Running Daemons

目标：从“能跑命令”进入“能管系统”。

实施内容：

1. Pebble init
新增 `system/init.peb`，负责：
- early boot
- mount proc/dev/tmp
- 启动 service manager
- 运行 profile / rc 脚本
- 启动 login shell

2. service manager
Pebble 侧管理：
- service unit state
- restart policy
- dependencies
- stdout/stderr log routing
- health / failure state

3. service CLI
- `initctl` 或 `svc`
- `start`
- `stop`
- `restart`
- `status`
- `enable`
- `disable`

4. logging
先做：
- `/var/log`
- per-service logs
- boot log
- ring buffer

完成标准：

- 系统可以无用户交互完成自举
- 长运行服务不再只是后台作业

## Phase 6: Security and Multi-User Skeleton

目标：没有权限模型，就不能叫现代 OS。

实施内容：

1. 用户/组基础对象
- uid
- gid
- supplementary groups
- passwd/group 文件格式

2. 文件 metadata 扩展
- owner
- group
- mode bits
- atime/mtime/ctime
- file type

3. 能力模型
如果完整 UNIX 权限太重，先做简化 capability：
- `CAP_FS_ADMIN`
- `CAP_PROC_ADMIN`
- `CAP_NET_ADMIN`
- `CAP_SERVICE`
- `CAP_TTY_RAW`

4. shell/session 安全边界
- 普通用户不能直接操作所有进程
- 服务运行身份可配置

完成标准：

- `stat` 能展示 owner/mode/type
- 系统命令按权限失败，而不是一律成功

## Phase 7: Filesystem Maturity and Mountable System Layout

目标：把当前路径 helper + VFS 模型提升到现代系统所需的文件系统语义。

实施内容：

1. inode-like metadata layer
即便底层仍由 Python 存储，也要在 Pebble 侧维护：
- stable file id
- metadata records
- directory entries
- links count

2. mount table
支持：
- root fs
- procfs
- tmpfs
- devfs placeholder
- system runtime mount

3. durability semantics
- sync/fsync
- crash-consistency strategy
- metadata write ordering
- journaling 先不做，至少要有 commit model

4. 特殊文件系统
优先：
- `procfs`
- `tmpfs`
- `devfs` placeholder

完成标准：

- mount 不再只是当前 `system/...` 的特殊 case
- 系统布局可组合、可观测

## Phase 8: Networking as a Pebble Service Layer

目标：网络必须作为 Pebble 定义的服务/ABI，而不是直接暴露 Python socket API。

实施内容：

1. 先做 net service，不直接做 full kernel socket stack
Pebble 侧定义：
- socket-like fd
- connect/bind/listen/accept/send/recv
- poll/select-style event

2. Python 只做 host socket substrate
3. DNS/HTTP/TCP 工具放在用户空间
4. 回环和本地 IPC 先于外网

完成标准：

- 网络 API 走统一 fd/syscall 模型
- 用户空间可写网络客户端/服务

## Phase 9: Package Management and System Evolution Toolchain

目标：让 PebbleOS 能自我分发、升级、构建。

实施内容：

1. 包格式
至少包含：
- metadata
- version
- ABI requirement
- files manifest
- scripts/hooks

2. 包管理命令
- `pkg install`
- `pkg remove`
- `pkg upgrade`
- `pkg search`
- `pkg verify`

3. 版本化 runtime ABI
每个系统包声明兼容 Pebble runtime ABI 版本

4. 自举工具链
优先做 Pebble 写成的：
- tokenizer/formatter/checker
- module inspector
- package builder
- simple bytecode compiler frontend

完成标准：

- 安装系统工具不再靠手动复制 `.peb`
- runtime ABI 变化可被包系统管理

## Phase 10: Shrink Python Further

目标：不是立刻去掉 Python，而是把 Python 压缩成可替换宿主。

实施内容：

1. 删除所有高层 shell policy from Python
2. 删除所有 job-control-specific Python public concepts
3. 把 Python 中剩余接口压到最小 primitive 集
4. 把 interpreter/bytecode state serialization 接口做稳定
5. 为未来替换成其他宿主语言留接口

完成标准：

- Python 文件主要描述 VM substrate，而不是 OS 行为
- OS 行为变更主要改 Pebble 文件

## Ordered Execution Plan

建议按下面顺序推进，每一步都能形成闭环。

1. ABI inventory and syscall design
输出一份正式 syscall/host ABI 规范文档，并标记现有 Python host_functions 的迁移归宿。

2. Runtime split
把 [`/Users/xulixin/LX_OS/pebble_system/runtime.peb`](/Users/xulixin/LX_OS/pebble_system/runtime.peb) 拆成 kernel/lib 层，先不改行为，只改结构。

3. Process table unification
把 `BackgroundJob` / `VMTask` 统一成单一进程模型，`ps/jobs/fg` 全部改走新表。

4. Exit status + wait + signal minimum
补齐 `waitpid`、exit code、`SIGINT`、`SIGTSTP`、`SIGCHLD`。

5. FD layer
实现 `open/read/write/close/stat/readdir` 和每进程 fd table。

6. Pipe and redirection
shell 获得 `|`, `>`, `<`, `2>`，并把核心命令改为流式。

7. External command model
把大部分命令迁移到 `/system/bin/*.peb`，shell 只保留内建。

8. Init/service manager
建立自动启动和守护进程管理。

9. Security skeleton
加 UID/GID/mode bits/capability。

10. Mount table and special FS
procfs/tmpfs/devfs placeholder 完整落地。

11. Networking
先 local IPC，再 TCP/HTTP 工具。

12. Package manager and Pebble self-hosting tools
开始让 PebbleOS 自己管理自己的演进。

## Concrete File / Module Targets

建议新增或重组这些路径：

- `/Users/xulixin/LX_OS/pebble_system/kernel/syscall.peb`
- `/Users/xulixin/LX_OS/pebble_system/kernel/proc.peb`
- `/Users/xulixin/LX_OS/pebble_system/kernel/fd.peb`
- `/Users/xulixin/LX_OS/pebble_system/kernel/fs.peb`
- `/Users/xulixin/LX_OS/pebble_system/kernel/sched.peb`
- `/Users/xulixin/LX_OS/pebble_system/kernel/term.peb`
- `/Users/xulixin/LX_OS/pebble_system/kernel/service.peb`
- `/Users/xulixin/LX_OS/pebble_system/bin/sh.peb`
- `/Users/xulixin/LX_OS/pebble_system/bin/ps.peb`
- `/Users/xulixin/LX_OS/pebble_system/bin/echo.peb`
- `/Users/xulixin/LX_OS/pebble_system/bin/find.peb`
- `/Users/xulixin/LX_OS/pebble_system/init.peb`
- `/Users/xulixin/LX_OS/docs/ABI.md`
- `/Users/xulixin/LX_OS/docs/PROCESS.md`
- `/Users/xulixin/LX_OS/docs/FILESYSTEM_V2.md`

Python 侧应逐步收敛为：

- `/Users/xulixin/LX_OS/pebble_bootloader/lang.py`
- `/Users/xulixin/LX_OS/pebble_bootloader/shell.py`
- `/Users/xulixin/LX_OS/pebble_bootloader/fs.py`

但其职责会被压缩。

## Test Cases and Scenarios

每阶段都必须扩测试，而不是只靠 README 运行样例。

### ABI / runtime split
- runtime 拆分后，现有 `help/ls/cd/run/exec/ps/jobs/fg` 行为不回归
- syscall 层错误码与异常边界一致
- Pebble 模块导入新路径结构后仍可启动 shell

### Process model
- 前台进程正常退出并返回 exit status
- 后台进程可 `wait`
- 子进程退出后进入 zombie，再被 reap
- `SIGINT` 只打到前台 process group
- `fg/bg/jobs/ps` 对 pipeline 和单进程都正确

### FD / pipe / redirection
- `echo hi > a.txt`
- `cat a.txt | wc`
- `grep x < in.txt > out.txt`
- `stderr` 可单独重定向
- 多级 pipeline 正确关闭 pipe ends，不死锁

### Shell / command model
- PATH 搜索正确
- 内建 `cd` 不生成外部进程
- 外部命令 exit code 传播正确
- 引号和空格解析与预期一致

### Service manager
- boot 启动核心服务
- 服务失败自动重启
- `status` 能看到 crash reason
- 服务日志落盘或进入 ring buffer

### Security
- 普通用户无法杀掉不属于自己的进程
- 文件 mode 生效
- service 可指定运行身份
- procfs 敏感信息权限受控

### Filesystem
- mount/umount 后路径解析正确
- procfs/tmpfs/devfs 与普通文件系统并存
- sync/crash-recovery 至少满足文档定义
- metadata 与 data 一致

### Networking
- loopback echo service
- client connect/send/recv
- non-blocking/poll 行为稳定
- 网络错误映射到统一 errno

### Package management
- 安装/卸载文件清单正确
- ABI 不匹配包被拒绝
- 升级保留配置策略明确
- verify 能发现损坏文件

## Assumptions and Defaults

- 默认接受阶段性不兼容，不优先维护历史 Pebble 程序无修改运行。
- 默认继续使用 Python 作为宿主 VM 和设备桥，不在本路线图里尝试“去 Python 解释器实现”。
- 默认优先统一 ABI 与进程/fd 模型，再补 Linux 风格 coreutils。
- 默认网络采用“Pebble 定义接口，Python 提供宿主 socket substrate”的方式，而不是直接暴露 Python socket。
- 默认 shell 未来以外部命令为主，内建最小化。
- 默认先做 capability + 简化 UNIX 权限，再考虑完整 POSIX 细节。
- 默认不追求二进制兼容 Linux；目标是行为模型和系统结构尽量相似。
- 默认 `/proc`, `/etc`, `/var`, `/tmp`, `/dev` 会作为系统布局的一部分逐步加入。
- 默认测试策略以 `python3 -m unittest discover -s tests` 为基线，并继续扩展 integration-style shell tests。

## First Milestone to Execute Next

建议立刻开始 Milestone 1，范围严格限定为“只做架构边界，不做大功能”：

1. 编写 ABI 设计文档，列出当前所有 host function 和目标 syscall 分类。
2. 把 `runtime.peb` 拆分为 `kernel/*` + `lib/*`，保持外部行为不变。
3. 在 Pebble 侧引入统一 `proc state` 和 `errno` 常量定义。
4. 给 `ps/jobs/fg/run/exec` 增加基于新结构的过渡适配层。
5. 为上述内容补测试，确保当前 83 个测试仍通过，并新增 ABI/模块拆分回归测试。

这一步完成后，后续每一阶段都会明显更顺，不会继续把设计债堆在单个 `runtime.peb` 和 `shell.py` 里。
