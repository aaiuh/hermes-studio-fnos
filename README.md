# hermes-studio-fnos

[Hermes Studio](https://github.com/EKKOLearnAI/ekko-studio)（`hermes-web-ui` + `hermes-agent`）的飞牛 fnOS 原生 fpk 封装。

三条能力，都已在本机验证：

| 能力 | 做法 |
| --- | --- |
| **fpk 内可直接更新控制台** | 控制台代码装进可写的 npm 全局前缀，装、读、更新三处指向同一路径 |
| **空白机器全新安装即可用** | 内嵌官方 Agent runtime（venv 为相对软链），安装时解压，不依赖外网 |
| **支持飞牛「应用数据备份」** | 提供 `cmd/backup_callback` / `cmd/restore_callback` 钩子，可在控制台备份与还原 |

不打包 Docker，复用系统 `nodejs_v24`（`install_dep_apps=nodejs_v24`）。

## 目录约定

| 内容 | 位置 | 升级时 |
| --- | --- | --- |
| 用户数据（profile、记忆、会话、技能、cron） | `@appshare/Hermes/.hermes` | 不动 |
| Studio 自身库与上传附件 | `@appshare/Hermes/.hermes-web-ui` | 不动 |
| 控制台代码 `hermes-web-ui` | `@appdata/Hermes/npm/lib/node_modules` | 重装 / 随上游更新 |
| Agent 运行时 `hermes-agent` | `@appcenter/Hermes/server/lib/node_modules` | 首次安装从包内解压；升级时暂存到 `@appshare/Hermes/.upgrade-keep` 再移回 |

`@appshare` 是 data-share，卸载与升级都不会删除它 —— 这是数据安全的底座。

## 为什么控制台能自更新

官方 fpk 把控制台放在 `@appcenter/<app>/server/lib/node_modules`，而 `npm install -g` 会装到 `@appdata/<app>/node`。Studio 自带的「检查更新」按 `npm root -g` 找安装目标、却从 `@appcenter` 启动，于是在 fnOS 上表现为：

- 点更新 → 提示成功 → 重启后版本没变；或者
- 更新中途起了个不完整的新进程，端口没人监听，Web UI 打不开，必须重启应用才恢复。

本包把两侧统一到同一处：

```
控制台代码  →  $TRIM_PKGVAR/npm/lib/node_modules/hermes-web-ui   （npm 前缀，可写）
启动读取    →  同一路径，找不到才回落包内副本
NPM_CONFIG_PREFIX 显式指向该前缀（系统 nodejs_v24 的默认前缀在其只读应用目录）
```

装、读、更新三处一致，fpk 内更新才成立。

## 安装

```
应用中心 → 手动安装 → 选择 Hermes.fpk
```

- 向导只有一项：Web 控制台端口（默认 `8648`）
- 依赖系统应用 `nodejs_v24`（已装则自动跳过）
- 装完等状态变「运行中」再点图标
- 默认账号 `admin / 123456`，登录后在「设置 → 模型」配置 API Key

安装回调依次做：复用系统 node → 解压包内 runtime 到 `server/lib/node_modules/hermes-agent` → `npm install --ignore-scripts` 装控制台 → 补 `node-pty` 的 linux-x64 预编译模块 → 重建 `server/bin` 软链 → 修正配置里硬编码的旧包路径。

> `node-pty@1.1.0` 的 npm 包不带 `linux-x64` 预编译，安装脚本会回退到 `node-gyp`，而飞牛没有 `g++`，整次 `npm install` 会回滚。因此本包固定用 `--ignore-scripts`，并在 `app/vendor/pty.node` 里带一份同版本预编译模块（x86-64，67,392 B）。

## 飞牛「应用数据备份」

包内提供两个钩子，飞牛控制台 **存储空间 → 应用数据备份** 会调用：

| 钩子 | 行为 |
| --- | --- |
| `cmd/backup_callback` | 先停服务保证 sqlite 一致 → 打包 → 自检关键文件 → 重新拉起服务；产物写入 `$TRIM_BACKUP_DIR/data/`，附 `backup-info.txt`（sha256 / 条目数 / 版本） |
| `cmd/restore_callback` | 校验归档可读 → 停服务 → **现有数据整体改名成 `.before-restore-<时间戳>`** → 解包 → 校验三件关键文件 → 修正归属与 MCP 路径 → 拉起服务；任何一步失败自动把旧数据挪回来 |

备份内容：`.hermes`（配置 / 记忆 / profiles / 技能 / 会话库 / cron / 插件）、`.hermes-web-ui`（控制台库与附件）、`.ssh`、`.git-credentials*` 等点文件。

排除项（纯缓存与可重新下载的内容）：`.hermes/{cache,logs,tools,lsp,state-snapshots,telemetry}`、`.cache`、`.npm`、`.pub-cache`、`.dartServer`、`.dart-tool`、`.android`、`.local`、`node_modules`、`__pycache__`、`*-wal`、`*-shm`。

实测（约 7.5 GB 原始数据）：压缩后约 **3.0 GB**，打包 56 秒，包内 3 万余条目；还原到空目录后 `config.yaml` 与各 profile 的 `USER.md` / `MEMORY.md` 逐字节一致。

## 自动出包

`.github/workflows/build.yml` 每 6 小时跑一次，**判据有两个**：

1. **官方 runtime 版本**变了 —— 取 `EKKOLearnAI/ekko-studio` 里最新的 `hermes-<ver>-runtime` Release，锚点记在 `upstream.txt`
2. **包内容签名**变了 —— 对 `manifest/cmd/config/wizard/app/build` 求 sha256，锚点记在 `pkgsig.txt`

任一变化 → 下载并校验官方 runtime → 写入版本 → 校验脚本与向导 → 打包 → 解包自检 → 发 Release `v<控制台版本>-<修订号>` → 提交锚点。

控制台在 Studio 内可以自更新，所以单纯的控制台小版本不必重打这个约 515 MB 的包；只有 runtime 或封装层内容变化才重打。

手动触发（`workflow_dispatch`）可指定 runtime tag、控制台版本、修订号，或用 `force` 强打。修订号自动取 `git ls-remote --tags` 里的最大值 +1。

## 已知限制

- **包体较大**：约 515 MB，因为内嵌了官方 Agent runtime。这是「全新安装可用」的代价
- **备份体积**：一份完整备份约 3 GB，取决于工作区大小
- **卸载不会清 `.hermes`**：数据目录始终保留；想彻底清空请自行删除 `@appshare/Hermes`
- **端口占用**：默认 8648，被占用时在安装向导或应用设置里改

## 许可

打包脚本与 fnOS 适配层为 MIT。Hermes Studio 与 Hermes Agent 遵循其上游许可，见 [EKKOLearnAI/ekko-studio](https://github.com/EKKOLearnAI/ekko-studio) 与 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)。

本仓库仅做飞牛 fnOS 的打包分发，不对软件本身的安全性与稳定性作保证。
