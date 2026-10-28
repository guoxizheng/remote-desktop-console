# 飞牛 fnOS 应用包 (.fpk) 构建说明

本目录用于把远程桌面控制台打包成飞牛 fnOS 可安装的 `.fpk`。

## 目录结构

```
fnos/
├── build.sh          # 一键构建脚本
├── gen_icons.py      # 生成 64/256 图标(纯标准库)
├── src/              # fpk 源模板
│   ├── manifest      # 应用元数据(名称/版本/端口/入口)
│   ├── ICON.PNG      # 包图标 64x64
│   ├── ICON_256.PNG  # 包图标 256x256
│   ├── cmd/          # 生命周期脚本(install/start/stop/...)
│   ├── config/       # privilege / resource 声明
│   ├── wizard/       # 安装向导(本应用无需输入)
│   └── app/ui/       # 桌面入口配置 + 入口图标
└── build/            # 构建产物(自动生成, 不入库)
```

## 构建

```bash
cd fnos
./build.sh
```

脚本会:

1. 生成图标(`src/ICON*.PNG`、`src/app/ui/images/icon_*.png`)
2. 把主程序(`server.py`、`web/`、`vendor/`)复制进 `build/app/`
3. 自动下载 `fnpack`(首次),校验并打包
4. 输出 `remote-desktop-console-<version>.fpk`

> 需要 Linux x86_64 环境;`vendor/` 若不存在,包内将不含 RDP 组件
> (安装后可在应用目录运行 `setup_vendor.sh` 补齐)

## 安装

飞牛 fnOS → 应用中心 → 右上角「手动安装」→ 选择 `.fpk` 文件。

安装后:

- 应用中心出现「远程桌面控制台」,可启动/停止
- 桌面入口打开 `http://<NAS_IP>:8389`
- 运行数据在 `/vol{n}/@appdata/remote-desktop-console`(hosts.json、日志、RDP 会话日志)

## 关键配置

| 文件 | 说明 |
|---|---|
| `src/manifest` | 应用名、版本、`service_port=8389`、入口 ID |
| `src/cmd/main` | start/stop/status 控制,端口取自 `TRIM_SERVICE_PORT` |
| `src/config/privilege` | `run-as: package`(独立应用用户运行) |
| `src/app/ui/config` | 桌面入口(浏览器新标签打开 `http://NAS:8389/`) |

## 升级版本

1. 修改 `src/manifest` 的 `version` 与 `changelog`
2. 重新 `./build.sh`
3. 用新 `.fpk` 覆盖安装(系统会执行 upgrade 脚本,自动停服→升级→启动)

## 注意事项

- 应用以独立用户(package)运行,终端里的命令也是该用户权限,不能用 `sudo`
- 端口 8389 需空闲;被占用时系统会提示端口检查失败
- 卸载时保留数据目录(`@appdata`),便于重装后恢复主机列表
