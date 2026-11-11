<div align="center">
<img src="logo.png" alt="logo" width="128">
<h1>astrbot_plugin_ip_monitor</h1>
<h3>本机IP变化监控</h3>

[![License](https://img.shields.io/badge/License-GPLv3-blue?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.14+-blue?style=flat-square)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/Framework-AstrBot-ff6b6b?style=flat-square)](https://github.com/AstrBotDevs/AstrBot)

</div>

## ✨ 功能

- 后台按可配置周期检测公网 IP
- IP 变化时向配置的 QQ 号发送**私聊**通知
- 可自定义通知文案模板（支持变量）
- 固定使用三家查询服务做故障转移：
  - [IP.SB](https://api.ip.sb/ip)（纯文本）
  - [ipwho.is](https://ipwho.is/)（JSON）
  - [ipapi.is](https://api.ipapi.is/)（JSON）

## ⬇️ 安装

1. 将本插件目录放到 AstrBot 的 `data/plugins/` 下，或通过 WebUI「从文件安装」上传 zip
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 在 WebUI 插件页重载本插件

## ⚙️ 配置说明

| 配置项 | 说明 | 默认 |
|--------|------|------|
| **启用 IP 监控** | 关闭后不再后台检测，命令仍可用 | 开启 |
| **检测周期（秒）** | 两次检测间隔，建议 60～3600 | 300 |
| **启动时若检测到变化则通知** | 与历史 IP 不同时是否通知 | 开启 |
| **通知目标 QQ 号** | 仅私聊。填写 QQ 号列表 | 空 |
| **消息平台 ID** | 拼装私聊会话用，一般填 `aiocqhttp` | `aiocqhttp` |
| **通知消息模板** | 支持变量，见下表 | 见默认 |
| **单次查询超时（秒）** | 每个服务超时 | 10 |

### 🔵 消息模板变量

| 变量 | 含义 |
|------|------|
| `{old_ip}` | 变化前的 IP |
| `{new_ip}` | 变化后的 IP |
| `{time}` | 检测时间 |
| `{hostname}` | 本机主机名 |
| `{interval}` | 当前检测周期（秒） |

## 🗨️ 命令

| 命令 | 权限 | 别名 | 说明 |
|------|------|------|------|
| `/ipstatus` | 管理员 | `ip状态` | 查看监控状态与通知 QQ 列表 |
| `/ipnow` | 管理员 | `当前ip`、`myip` | 立即查询当前公网 IP |
| `/ipcheck` | 管理员 | `检测ip`、`强制检测` | 立即检测，变化则私聊通知 |
| `/iptest` | 管理员 | `测试通知` | 向配置的 QQ 发测试私聊 |

## 🛑 注意事项

1. 检测周期不要设得过短，避免触发服务限流
2. 三个服务全部失败时不会误判为 IP 变化
3. 首次启动只记录 IP，不发「变化」通知
4. 本插件**不会**向任何群发送消息

## ⚖️ 许可证

本项目采用 GPLv3 许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。