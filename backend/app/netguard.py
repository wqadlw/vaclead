"""出站请求安全守卫（SPEC-RADAR：官网监控雷达的用户自添加 URL 边界）。

与 notify.channels._webhook_guard 的差异：webhook 有官方域名白名单；
官网监控的 URL 千差万别——只做协议+解析 IP 全公网+禁重定向三层
（Mimosa SSRF 口径），杜绝凭据被盗后探测内网/云元数据。"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def guard_public_https(url: str) -> str:
    """校验 https + host 解析后全部为公网 IP；返回小写 host。

    违规抛 ValueError（调用方转可读错误）。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("URL 必须 https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL 缺少主机名")
    for _, _, _, _, sa in socket.getaddrinfo(host, 443):
        ip = ipaddress.ip_address(sa[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise ValueError(f"URL 解析到非公网地址: {ip}")
    return host
