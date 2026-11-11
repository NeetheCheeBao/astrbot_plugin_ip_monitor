from __future__ import annotations

import asyncio
import json
import re
import socket
from datetime import datetime
from typing import Optional

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain, filter, AstrMessageEvent
from astrbot.api.star import Context, Star

# IP.SB 返回纯文本；ipwho.is / ipapi.is 返回 JSON，字段为 ip
IP_SERVICES = [
    {"name": "IP.SB", "url": "https://api.ip.sb/ip", "type": "text"},
    {"name": "ipwho.is", "url": "https://ipwho.is/", "type": "json"},
    {"name": "ipapi.is", "url": "https://api.ipapi.is/", "type": "json"},
]

QQ_RE = re.compile(r"^\d{5,12}$")


def _normalize_qq_list(raw) -> list[str]:
    """把配置里的 QQ 列表整理成纯数字字符串列表。"""
    result: list[str] = []
    if not raw:
        return result
    if isinstance(raw, str):
        raw = [raw]
    for item in raw:
        if item is None:
            continue
        text = str(item).strip()
        parts = re.split(r"[,，\s]+", text)
        for p in parts:
            p = p.strip()
            if QQ_RE.match(p):
                result.append(p)
    seen = set()
    unique = []
    for q in result:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


class IPMonitorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_ip: Optional[str] = None
        self._last_check_time: Optional[datetime] = None
        self._check_lock = asyncio.Lock()
        self._platform_id: str = "aiocqhttp"  # 自动获取后的缓存

    async def initialize(self):
        """插件加载后启动后台检测任务。"""
        self._last_ip = await self.get_kv_data("last_ip", None)
        self._platform_id = self._auto_get_platform_id()
        logger.info(
            f"[IPMonitor] 初始化完成，上次记录 IP: {self._last_ip or '无'}，"
            f"自动识别平台 ID: {self._platform_id}"
        )

        if self.config.get("enable", True):
            self._stop_event.clear()
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info("[IPMonitor] 后台监控任务已启动")
        else:
            logger.info("[IPMonitor] 监控未启用（配置 enable=false）")

    async def terminate(self):
        """插件卸载/停用时停止后台任务。"""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[IPMonitor] 已停止")

    def _auto_get_platform_id(self) -> str:
        """自动获取可用的消息平台 ID（优先 aiocqhttp）。"""
        try:
            platforms = self.context.platform_manager.get_insts()
            # 优先找 name 为 aiocqhttp 的平台
            for p in platforms:
                try:
                    meta = p.meta()
                    if meta.name == "aiocqhttp":
                        pid = meta.id or "aiocqhttp"
                        logger.debug(f"[IPMonitor] 自动匹配到 aiocqhttp 平台，ID={pid}")
                        return pid
                except Exception:
                    continue
            # 没有 aiocqhttp 时，取第一个有 id 的平台
            for p in platforms:
                try:
                    meta = p.meta()
                    if meta.id:
                        logger.warning(
                            f"[IPMonitor] 未找到 aiocqhttp，回退使用平台 {meta.name} (ID={meta.id})"
                        )
                        return meta.id
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[IPMonitor] 自动获取平台失败: {e}，使用默认 aiocqhttp")
        return "aiocqhttp"

    def _parse_ip_from_response(self, service: dict, text: str) -> Optional[str]:
        """根据服务类型从响应中提取 IP。"""
        text = (text or "").strip()
        if not text:
            return None

        if service["type"] == "text":
            candidate = text.splitlines()[0].strip()
            if candidate and len(candidate) < 64 and " " not in candidate:
                return candidate
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        ip = data.get("ip")
        if isinstance(ip, str) and ip.strip():
            return ip.strip()

        nested = data.get("data")
        if isinstance(nested, dict):
            ip = nested.get("ip")
            if isinstance(ip, str) and ip.strip():
                return ip.strip()
        return None

    async def _fetch_public_ip(self) -> Optional[str]:
        """依次尝试 IP.SB / ipwho.is / ipapi.is 获取公网 IP。"""
        timeout = self.config.get("request_timeout", 10)
        headers = {
            "User-Agent": "AstrBot-IPMonitor/1.1",
            "Accept": "application/json, text/plain, */*",
        }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=headers,
        ) as session:
            for service in IP_SERVICES:
                try:
                    async with session.get(service["url"]) as resp:
                        if resp.status != 200:
                            logger.debug(
                                f"[IPMonitor] {service['name']} HTTP {resp.status}"
                            )
                            continue
                        body = await resp.text()
                        ip = self._parse_ip_from_response(service, body)
                        if ip:
                            logger.debug(
                                f"[IPMonitor] 通过 {service['name']} 获取 IP: {ip}"
                            )
                            return ip
                except Exception as e:
                    logger.debug(f"[IPMonitor] 查询 {service['name']} 失败: {e}")
                    continue
        return None

    def _build_private_umo(self, qq: str) -> str:
        """拼装私聊 UMO，仅 FriendMessage，不支持群。使用自动获取的平台 ID。"""
        platform = self._platform_id or "aiocqhttp"
        return f"{platform}:FriendMessage:{qq}"

    async def _check_and_notify(
        self, force_notify: bool = False
    ) -> tuple[Optional[str], bool]:
        """执行一次检测。返回 (当前IP, 是否发生了变化)。"""
        async with self._check_lock:
            new_ip = await self._fetch_public_ip()
            self._last_check_time = datetime.now()

            if new_ip is None:
                logger.warning("[IPMonitor] 所有 IP 查询服务均失败")
                return None, False

            changed = self._last_ip is not None and new_ip != self._last_ip
            is_first = self._last_ip is None

            if is_first:
                logger.info(f"[IPMonitor] 首次记录公网 IP: {new_ip}")
                await self.put_kv_data("last_ip", new_ip)
                self._last_ip = new_ip
                return new_ip, False

            if changed or force_notify:
                old_ip = self._last_ip or "未知"
                logger.info(f"[IPMonitor] IP 变化: {old_ip} -> {new_ip}")
                await self.put_kv_data("last_ip", new_ip)
                self._last_ip = new_ip

                should_notify = force_notify or self.config.get(
                    "notify_on_startup", True
                )
                if should_notify:
                    await self._send_notifications(old_ip, new_ip)
                else:
                    logger.info(
                        "[IPMonitor] IP 已变化，但 notify_on_startup=false，跳过通知"
                    )
                return new_ip, True

            return new_ip, False

    async def _send_notifications(self, old_ip: str, new_ip: str):
        """向配置的 QQ 号发送私聊通知（不支持群聊）。"""
        qq_list = _normalize_qq_list(self.config.get("notify_qq_list"))
        if not qq_list:
            logger.warning("[IPMonitor] IP 已变化，但未配置任何通知 QQ 号")
            return

        template = self.config.get(
            "message_template",
            "⚠️ 公网 IP 已变更\n旧 IP：{old_ip}\n新 IP：{new_ip}\n时间：{time}\n主机：{hostname}",
        )
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = "unknown"

        msg = template.format(
            old_ip=old_ip,
            new_ip=new_ip,
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            hostname=hostname,
            interval=self.config.get("check_interval_seconds", 300),
        )

        chain = MessageChain().message(msg)
        success = 0
        for qq in qq_list:
            umo = self._build_private_umo(qq)
            try:
                await self.context.send_message(umo, chain)
                success += 1
                logger.info(f"[IPMonitor] 已私聊通知 QQ {qq} ({umo})")
            except Exception as e:
                logger.error(f"[IPMonitor] 通知 QQ {qq} 失败: {e}")

        logger.info(f"[IPMonitor] 私聊通知完成，成功 {success}/{len(qq_list)}")

    async def _monitor_loop(self):
        """后台循环：按配置周期检测。"""
        await asyncio.sleep(5)

        while not self._stop_event.is_set():
            try:
                if self.config.get("enable", True):
                    await self._check_and_notify()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[IPMonitor] 检测循环异常: {e}", exc_info=True)

            interval = max(30, int(self.config.get("check_interval_seconds", 300)))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                continue

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("ipstatus", alias=["ip状态", "ip_status"])
    async def cmd_status(self, event: AstrMessageEvent):
        """查看当前公网 IP 监控状态（仅私聊管理员）"""
        enabled = self.config.get("enable", True)
        interval = self.config.get("check_interval_seconds", 300)
        qq_list = _normalize_qq_list(self.config.get("notify_qq_list"))
        last_check = (
            self._last_check_time.strftime("%Y-%m-%d %H:%M:%S")
            if self._last_check_time
            else "尚未检测"
        )
        # 实时再刷新一次平台 ID，防止运行中平台变更
        platform = self._auto_get_platform_id()
        self._platform_id = platform

        text = (
            f"📡 IP 监控状态\n"
            f"启用：{'是' if enabled else '否'}\n"
            f"检测周期：{interval} 秒\n"
            f"上次记录 IP：{self._last_ip or '无'}\n"
            f"上次检测时间：{last_check}\n"
            f"平台 ID（自动）：{platform}\n"
            f"通知 QQ 数：{len(qq_list)}"
            + (f"\nQQ：{', '.join(qq_list)}" if qq_list else "")
        )
        yield event.plain_result(text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("ipnow", alias=["当前ip", "myip"])
    async def cmd_now(self, event: AstrMessageEvent):
        """立即查询当前公网 IP（仅私聊管理员，不触发通知）"""
        yield event.plain_result("正在查询公网 IP…")
        ip = await self._fetch_public_ip()
        if ip:
            yield event.plain_result(
                f"当前公网 IP：{ip}\n记录中的 IP：{self._last_ip or '无'}"
            )
        else:
            yield event.plain_result(
                "查询失败。已尝试：IP.SB / ipwho.is / ipapi.is，请检查网络。"
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("ipcheck", alias=["检测ip", "强制检测"])
    async def cmd_check(self, event: AstrMessageEvent):
        """立即执行一次检测（仅私聊管理员），若 IP 变化则私聊通知"""
        yield event.plain_result("正在执行 IP 检测…")
        ip, changed = await self._check_and_notify(force_notify=False)
        if ip is None:
            yield event.plain_result("检测失败，无法获取公网 IP。")
        elif changed:
            yield event.plain_result(
                f"检测到 IP 变化！当前 IP：{ip}\n已向配置的 QQ 发送私聊通知。"
            )
        else:
            yield event.plain_result(f"IP 未变化。当前 IP：{ip}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("iptest", alias=["测试通知"])
    async def cmd_test_notify(self, event: AstrMessageEvent):
        """向配置的 QQ 发送测试私聊通知（仅私聊管理员）"""
        qq_list = _normalize_qq_list(self.config.get("notify_qq_list"))
        if not qq_list:
            yield event.plain_result(
                "未配置任何通知 QQ 号，请先在插件配置中填写「通知目标 QQ 号」。"
            )
            return

        ip = self._last_ip or (await self._fetch_public_ip()) or "未知"
        await self._send_notifications(ip, ip)
        yield event.plain_result(
            f"已向 {len(qq_list)} 个 QQ 发送测试私聊通知（IP：{ip}）。"
        )