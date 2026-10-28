# 局域网远程桌面控制台 (LAN Remote Desktop Console)

[![CI](https://github.com/guoxizheng/remote-desktop-console/actions/workflows/ci.yml/badge.svg)](https://github.com/guoxizheng/remote-desktop-console/actions/workflows/ci.yml)

在 NAS 上运行一个轻量 Web 服务,用浏览器直接操作局域网内 Ubuntu 的图形桌面。
纯 Python 标准库实现,**零依赖**(无需 apt 安装任何包)。

## 功能

- 浏览器里管理远程主机(添加 / 编辑 / 删除)
- 内置 **WebSocket → TCP 桥接**(相当于自带 websockify),并内嵌 noVNC 前端
- 一键「连接桌面」:新标签页打开远程桌面,支持自适应缩放
- **RDP 支持**:目标机跑 xrdp(或 GNOME RDP)时,一键把 RDP 会话转成
  浏览器可看的桌面(自带 FreeRDP 2.11.7 + KasmVNC 虚拟显示器,无需 root)
- **内置 Web 终端**:浏览器里直接操作 NAS 命令行(xterm.js + PTY;
  容器内无 PTY 时自动降级为管道模式)
- 端口连通性测试(VNC / RDP)、活动连接与流量统计、运行日志
- 可选访问令牌(`--token`),防止局域网内其他人随意使用

## 文件结构

```
remote-desktop-console/
├── server.py        # 主程序(单文件, 纯标准库)
├── start.sh         # 启动/停止/状态/日志管理脚本
├── setup_vendor.sh  # 一次性下载 FreeRDP/x11vnc 二进制到 vendor/
├── web/
│   ├── index.html   # 中文控制台页面
│   ├── term.html    # Web 终端页面(xterm.js)
│   ├── vendor/xterm # xterm.js 6.0 + 样式(随仓库)
│   └── novnc/       # 内嵌 noVNC 前端(v1.3.0)
├── vendor/          # setup_vendor.sh 生成, 不入库
│   ├── rdp/         # FreeRDP 2.11.7 客户端 + 库
│   └── vnc/         # x11vnc + libvncserver/libvncclient
├── data/            # 运行时数据, 不入库
│   ├── hosts.json   # 主机列表(含密码, 请勿外泄)
│   ├── server.log   # 运行日志
│   └── rdp_*.log    # RDP 会话日志
└── LICENSE          # MIT
```

## 快速开始

```bash
git clone <你的仓库地址>
cd remote-desktop-console

# 一次性: 下载内置的 FreeRDP / x11vnc 组件到 vendor/
./setup_vendor.sh

# 启动(后台常驻)
./start.sh start

# 或前台运行调试
python3 server.py --port 8389
```

然后在**任意电脑的浏览器**打开:

```
http://<NAS的局域网IP>:8389
```

## 飞牛 fnOS 安装包 (.fpk)

**直接安装**:仓库已附带构建好的安装包,下载后到飞牛「应用中心 → 右上角手动安装」即可:

- [`release/remote-desktop-console-1.0.0.fpk`](release/remote-desktop-console-1.0.0.fpk)(约 3.5 MB,已内置 RDP/VNC 组件)

自行构建:在 `fnos/` 下执行,首次会自动下载 fnpack:

```bash
cd fnos
./build.sh        # 输出 remote-desktop-console-<版本>.fpk
```

安装:飞牛 fnOS → 应用中心 → 右上角「手动安装」→ 选择 `.fpk`。

安装后应用中心出现「远程桌面控制台」,桌面入口打开 `http://<NAS_IP>:8389`;
数据保存在 `/vol{n}/@appdata/remote-desktop-console`(hosts.json、日志)。

详见 [fnos/README.md](fnos/README.md)。

## 目标 Ubuntu 端设置(必须)

被控那台 Ubuntu 需要开启远程桌面(GNOME 自带,Ubuntu 22.04+):

1. 打开 **设置 → 共享 → 远程桌面**
2. 打开「远程桌面」开关,勾选 **VNC**
3. 设置一个 VNC 密码(建议 8 位以内,RFB 协议限制)
4. 查看本机 IP:`ip -4 addr show`

如果系统较老(20.04 及以下,没有该选项),可以在目标机装 VNC 服务:

```bash
sudo apt install -y tigervnc-standalone-server
tigervncserver -localhost no :1
```

或改用 xrdp(RDP 方式,见下一节)。

## 使用说明

1. 控制台点「＋ 添加主机」,填名称、IP、VNC 端口(默认 5900),VNC 密码可选
2. 点「测试」确认端口可达(分别显示 VNC / RDP 延迟)
3. 点「**连接桌面**」(VNC)→ 新标签页自动打开远程桌面,输入 VNC 密码即可操作
4. 需要 RDP 时:编辑主机补充 RDP 用户名/密码,点「**连接 RDP**」
5. 点工具栏「**🖥️ 终端**」→ 新标签页打开 Web 终端,可执行 NAS 上的命令
6. 关闭标签页即断开,「活动桥接」「RDP 会话」面板可实时查看状态与流量

## Web 终端

- 后端用 `pty` 拉起登录 shell,浏览器通过 xterm.js 渲染
- 若运行环境禁用了 PTY(常见于容器),自动降级为**管道模式**:
  支持普通命令(cd、ls、systemctl 等),不支持 vim/top 等全屏程序
- 终端以运行本程序的用户身份执行,`sudo` 需自行配置权限
- 关闭页面/断开即结束 shell 进程,不会残留
- 不需要终端时:`RDC_NO_TERMINAL=1 ./start.sh restart` 禁用(或直接
  `python3 server.py --no-terminal`)

## RDP 支持(已内置,无需 root)

目标机使用 **xrdp**(RDP 协议)时,控制台可一键桥接,原理:

```
浏览器(noVNC) ⇄ WebSocket ⇄ 本程序 ⇄ x11vnc ⇐ Xvnc(:9x 虚拟显示器) ⇐ xfreerdp ⇐ 目标机 xrdp
```

NAS 端组件已随程序自带,无需 `sudo apt`:

- `vendor/rdp/` —— FreeRDP 2.11.7 客户端 + 库(解压自 Debian 软件包)
- `vendor/vnc/` —— x11vnc + libvncserver/libvncclient(标准 RFB 输出)
- `Xvnc` —— 系统自带 KasmVNC,仅用作虚拟显示器(X 服务器)

> 注意:KasmVNC 自带的 RFB 输出对 noVNC 不兼容,所以额外用 x11vnc
> 挂到同一个 X 显示上输出标准 RFB,noVNC 才能正常出画面。

使用步骤:

1. 目标机安装并启动 xrdp:
   ```bash
   sudo apt install -y xrdp
   sudo systemctl enable --now xrdp
   ```
2. 目标机需有可登录的桌面会话(如已安装 `xfce4` / `gnome`)
3. 控制台编辑主机,填写 **RDP 用户名** 和 **RDP 密码**
4. 点「**连接 RDP**」→ 程序自动启动虚拟显示器 + x11vnc + xfreerdp,
   浏览器打开桌面
5. 「RDP 会话」面板可查看/停止会话

说明:

- 目标机如果是 Ubuntu 22.04+ 的 GNOME 远程桌面,也可直接勾选 RDP 开关
- xrdp 自签证书已用 `/cert:ignore` 自动接受
- 目标机 HOME 无写权限时 xfreerdp 会证书初始化失败,程序已自动指定可写 HOME
- RDP 密码保存在 `data/hosts.json`,仅限可信局域网

## 依赖与许可

运行环境(均为系统自带或 setup_vendor.sh 下载):

| 组件 | 来源 | 许可 |
|------|------|------|
| Python 3.9+ | 系统 | PSF |
| Xvnc / Xvfb | 系统(KasmVNC 或 xorg) | GPL / MIT 等 |
| FreeRDP 2.11.7 | vendor/rdp(setup_vendor.sh 下载) | Apache-2.0 |
| x11vnc + libvncserver | vendor/vnc(setup_vendor.sh 下载) | GPL-2.0 |
| noVNC 1.3.0 | web/novnc(随仓库) | MPL-2.0 |
| xterm.js 6.0 | web/vendor/xterm(随仓库) | MIT |

本仓库自身代码采用 **MIT** 许可,详见 [LICENSE](LICENSE)。

## 安全说明

- 默认监听 `0.0.0.0:8389`,局域网内任何设备都可访问,**仅建议用于可信局域网**
- 建议加令牌启动:`RDC_TOKEN=你的密码 ./start.sh start`
- ⚠️ **Web 终端 = 一个可执行命令的 shell**:未设令牌时局域网内任何人都能拿到
  NAS 上的命令行;务必设置令牌,或 `RDC_NO_TERMINAL=1` 直接禁用
- VNC 密码会保存在 `data/hosts.json`(用于自动填入),请勿把该目录暴露到公网
- VNC 认证在浏览器与目标机之间完成,服务端只做字节转发
- 如需公网访问,请配合 WireGuard / Tailscale 等 VPN 使用,不要直接暴露端口

## 开机自启

NAS 上无 systemd 权限时,用 crontab:

```bash
crontab -e
# 加入一行:
@reboot /path/to/remote-desktop-console/start.sh start
```

## 常见问题

| 现象 | 处理 |
|------|------|
| 连接后黑屏 | 目标机屏幕锁定/无图形会话;先在本机登录一次桌面,或换用 x11vnc 方案 |
| 提示连接失败 | 用控制台「测试」确认 VNC 端口可达;确认目标机防火墙放行 5900 |
| VNC 密码错误 | GNOME 远程桌面密码最多 8 位;重新设置后更新主机配置 |
| RDP 启动失败 | 查看 `data/rdp_<主机id>.log`:常见为目标机 xrdp 未启动或账号密码错误 |
| RDP 会话「进程已退出」 | 点「停止 RDP」清理,确认目标机 `systemctl status xrdp` 正常后重试 |
| 页面打不开 | 确认 `./start.sh status` 为运行中,浏览器访问 `http://NAS_IP:8389` |
| 想看实时日志 | `./start.sh logs` 或控制台底部「运行日志」 |
