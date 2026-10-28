# NAS 上自建远程桌面控制台:浏览器直连 VNC/RDP,还能开 Web 终端(附飞牛 fpk 安装包)

> 不到 1000 行纯 Python 标准库,零依赖、零 root,把「远程桌面 + Web 终端」装进你的 NAS。

## 一、为什么又造一个轮子

家里 / 公司有一台飞牛 fnOS NAS,还有几台 Ubuntu 机器。平时想远程操作它们,试过的方案各有痛点:

- **TeamViewer / 向日葵 / ToDesk**:要装客户端、走公网中转,免费版还有并发和画质限制;
- **x11vnc / TigerVNC + 客户端**:每台机器都要配,换个设备就得再装一遍;
- **Guacamole**:功能强,但依赖 Java + guacd,对 NAS 来说太重。

我的需求其实很简单:**同一局域网内,打开浏览器,点一下,就能操作另一台机器的桌面**;最好还能顺手在 NAS 上开个终端。

于是我写了一个小小的控制台:

> **github.com/guoxizheng/remote-desktop-console**

纯 Python 标准库实现(无 pip 依赖、无需 root),自带 WebSocket→TCP 桥接和 noVNC 前端,还打包成了飞牛 fnOS 的 `.fpk` 安装包。

## 二、它长什么样

控制台是一个单页应用,主要做三件事:

1. **主机管理**:添加 / 编辑 / 删除局域网主机,一键测试端口连通性;
2. **远程桌面**:VNC 直连,或把目标机的 RDP 会话桥接成浏览器可看的画面;
3. **Web 终端**:直接在浏览器里跑 NAS 上的命令。

功能清单:

- WebSocket → TCP 内置桥接(不需要 websockify)
- 内嵌 noVNC 1.3.0,支持自适应缩放、剪贴板、全屏
- RDP 支持:目标机跑 xrdp 时,一键把 RDP 转成 Web 可看的桌面
- 内置 Web 终端(xterm.js + PTY,容器环境自动降级为管道模式)
- 可选访问令牌、活动连接统计、运行日志
- 一键打成飞牛 fnOS `.fpk`,应用中心手动安装

## 三、架构

```text
                    ┌──────────────────────────────┐
   浏览器            │        NAS (fnOS)            │
 ┌─────────┐        │  ┌────────────────────────┐  │
 │ 控制台   │◄──HTTP─┼─►│ server.py (纯标准库)    │  │
 │ 页面     │        │  │  · 静态文件 / JSON API  │  │
 └─────────┘        │  │  · WebSocket→TCP 桥接   │  │
 ┌─────────┐  WS    │  └───────────┬────────────┘  │
 │ noVNC   │◄───────┼──────────────┘               │
 └─────────┘        │        │ TCP                   │
                    └────────┼──────────────────────┘
                             ▼
              ┌───────────────────────────┐
              │ 目标 Ubuntu               │
              │  · VNC 5900 (GNOME 远程桌面)│
              │  · 或 xrdp 3389           │
              └───────────────────────────┘
```

RDP 模式下,NAS 本地再多一层转换:

```text
浏览器(noVNC) ⇄ WebSocket ⇄ server.py ⇄ x11vnc ⇐ Xvnc(虚拟显示器) ⇐ xfreerdp ⇐ 目标机 xrdp
```

## 四、几个实现上的坑

### 1. 自己实现 WebSocket→TCP 桥接

noVNC 通过 WebSocket 连接 VNC 服务器,通常要配套跑一个 websockify。既然目标是「零依赖」,我直接在 `http.server` 上实现了 RFC 6455:

- 握手:计算 `Sec-WebSocket-Accept`;
- 帧解析:处理掩码、126/127 扩展长度、分片、ping/pong、close;
- 转发:一个线程读 TCP 写 WS 帧,主线程读 WS 帧写 TCP。

实测 1600×900 的桌面推流很稳,代码不到 150 行。

### 2. RDP 桥接:组件缺失 + 两个隐蔽的坑

NAS 上只有 FreeRDP 的库、没有客户端,我把 Debian 仓库里的 `freerdp2-x11` 等 deb 解压进 `vendor/` 直接用(免 root)。虚拟显示器用系统自带的 KasmVNC `Xvnc`。

结果踩了两个坑:

- **HOME 不可写**:xfreerdp 启动时报 `error creating directory .../.config/freerdp`,证书存储初始化失败,安全协商直接挂。解决:给子进程指定可写 HOME(`data/home`)。
- **KasmVNC 的 RFB 不兼容标准客户端**:它走自家协议,标准 RFB 客户端发 `FramebufferUpdateRequest` 后拿不到任何画面。解决:再挂一个 `x11vnc` 到同一个 X 显示上,输出标准 RFB,noVNC 才正常出图。

### 3. Web 终端:PTY 与管道双模式

后端用 `pty.openpty()` 拉起登录 shell,前端 xterm.js 渲染。但很多容器环境禁用了 PTY(`/dev/ptmx` 权限拒绝),于是加了降级:检测到 PTY 不可用就用 `bash -i` + 管道,前端本地回显、`\r` 转 `\n`、Ctrl+C 转发 SIGINT。普通命令完全可用,只是不支持 vim/top 这类全屏程序。

### 4. 打成飞牛 fpk

按照飞牛[应用开发文档](https://developer.fnnas.com/)的规范,用官方 `fnpack` 打包:

```text
myapp/
├── manifest          # 应用元数据(名称/版本/端口/入口)
├── ICON.PNG          # 64x64
├── ICON_256.PNG      # 256x256
├── cmd/              # install/start/stop/status/upgrade/uninstall 生命周期
├── config/           # privilege / resource
├── wizard/           # 安装向导
└── app/              # 应用文件(server.py、web、vendor)
```

生命周期脚本 `cmd/main` 负责 start/stop/status,端口取自 `TRIM_SERVICE_PORT`,数据目录用 `TRIM_PKGVAR`(安装后落在 `/vol{n}/@appdata/<appname>`),升级、重装都不丢数据。

## 五、部署

### 方式一:飞牛 fpk(推荐)

下载 `release/remote-desktop-console-1.0.0.fpk`,飞牛 **应用中心 → 右上角手动安装**。

### 方式二:源码

```bash
git clone https://github.com/guoxizheng/remote-desktop-console.git
cd remote-desktop-console

./setup_vendor.sh    # 一次性地下载 FreeRDP / x11vnc 组件(可选, RDP 用)
./start.sh start     # 后台启动

# 浏览器打开
http://<NAS_IP>:8389
```

被控 Ubuntu 侧(GNOME 自带远程桌面,22.04+):

1. 设置 → 共享 → 远程桌面 → 打开,勾选 VNC,设置密码;
2. 查看 IP:`ip -4 addr show`;
3. 在控制台添加主机,点「连接桌面」即可。

xrdp 方案:

```bash
sudo apt install -y xrdp
sudo systemctl enable --now xrdp
```

在控制台为主机填好 RDP 用户名/密码,点「连接 RDP」。

## 六、安全提醒

- 默认监听 `0.0.0.0:8389`,**只建议在可信局域网使用**;
- 建议加令牌启动:`RDC_TOKEN=你的密码 ./start.sh start`;
- Web 终端等于一个可执行命令的 shell,不需要就 `RDC_NO_TERMINAL=1` 关掉;
- 公网访问请走 WireGuard / Tailscale 等 VPN,不要把端口直接暴露出去。

## 七、仓库

- GitHub:https://github.com/guoxizheng/remote-desktop-console
- 安装包:`release/remote-desktop-console-1.0.0.fpk`
- 协议:MIT

如果你也在用飞牛 NAS,或者有多台需要远程操作的 Linux 机器,可以试试这个思路——一个浏览器窗口,一台 NAS,搞定内网所有桌面。

欢迎提 Issue 和 Star ⭐

---

**标签**:NAS、飞牛fnOS、远程桌面、VNC、RDP、Python、Web 终端、自建服务
