# -*- coding: utf-8 -*-
"""
OA 接口基类

所有自动生成的 OA 接口类的公共基类。
提供统一的 HTTP 请求、会话管理、错误处理。

兼容: Python 3.8+, Windows 7
"""
import json
import os
import requests
from typing import Optional, Dict, Any


class BaseOAInterface(object):
    """OA 系统接口基类"""

    # 默认超时（秒）
    DEFAULT_TIMEOUT = 30

    # 默认重试次数
    DEFAULT_RETRIES = 2

    def __init__(self, base_url, timeout=None, retries=None, site_id=None):
        # type: (str, Optional[int], Optional[int], Optional[str]) -> None
        """
        初始化接口。

        Args:
            base_url: 系统基础 URL
            timeout: 请求超时时间（秒）
            retries: 失败重试次数
            site_id: 站点标识（用于自动加载凭证）
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.retries = retries if retries is not None else self.DEFAULT_RETRIES
        self.site_id = site_id or self._infer_site_id()

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 6.1; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/109.0.0.0 Safari/537.36'
            ),
            'X-Requested-With': 'XMLHttpRequest',
        })

        self._logged_in = False

    def _infer_site_id(self):
        # type: () -> str
        """从类名推断 site_id: MailOA → mail_oa"""
        import re
        name = self.__class__.__name__
        s1 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
        s2 = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s1).lower()
        return s2

    def _request(self, method, path, **kwargs):
        # type: (str, str, **Any) -> dict
        """
        统一请求方法。

        Args:
            method: HTTP 方法
            path: 请求路径（相对于 base_url）
            **kwargs: 传递给 requests.request 的额外参数

        Returns:
            dict: 响应 JSON

        Raises:
            requests.HTTPError: HTTP 错误
            requests.ConnectionError: 连接错误
            requests.Timeout: 超时
        """
        url = '{}{}'.format(self.base_url, path)

        # 设置超时
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout

        # 重试逻辑
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
                resp.raise_for_status()

                try:
                    data = resp.json()
                    # 确保返回格式一致：JSON dict 原样返回，其他类型包装
                    if isinstance(data, dict):
                        return data
                    return {
                        'status_code': resp.status_code,
                        'text': str(data) if data is not None else '',
                        'data': data,
                    }
                except (json.JSONDecodeError, ValueError):
                    return {
                        'status_code': resp.status_code,
                        'text': resp.text,
                    }

            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = e
                if attempt < self.retries:
                    import time
                    time.sleep(1 * (attempt + 1))
                continue

            except requests.HTTPError as e:
                # 4xx 错误不重试
                if e.response is not None and 400 <= e.response.status_code < 500:
                    raise
                last_error = e
                if attempt < self.retries:
                    import time
                    time.sleep(1 * (attempt + 1))
                continue

        if last_error:
            raise last_error

        return {}

    def _load_credentials(self, config_path=None):
        # type: (Optional[str]) -> Dict[str, str]
        """
        从配置文件加载凭证。

        配置文件格式 (JSON):
        {
            "username": "xxx",
            "password": "xxx",
            "token": "xxx"
        }

        默认路径: oa_sites/<site_id>/credentials.json（与接口代码同目录）

        Args:
            config_path: 配置文件路径，默认自动推断

        Returns:
            dict: 凭证字典
        """
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                self.site_id,
                'credentials.json'
            )

        if not os.path.exists(config_path):
            return {}

        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _encrypt_password(self, password):
        # type: (str) -> str
        """
        密码加密钩子 - 默认不加密，子类可覆盖。

        大部分 OA 系统前端会对密码做加密（MD5/SHA1/RSA/AES/SM2）。
        如果 HAR 解析检测到加密特征，生成的接口类会自动覆盖此方法。
        也可以手动修改 interface.py 中的该方法实现加密逻辑。

        常见实现：
            MD5:    return hashlib.md5(password.encode()).hexdigest()
            SHA1:   return hashlib.sha1(password.encode()).hexdigest()
            SHA256: return hashlib.sha256(password.encode()).hexdigest()
            Base64: return base64.b64encode(password.encode()).decode()

        Args:
            password: 明文密码

        Returns:
            str: 加密后的密码（默认原样返回）
        """
        return password

    def _load_cookies_from_credentials(self, creds):
        # type: (Dict[str, Any]) -> bool
        """
        从凭证中加载 Cookie 到 session（兜底登录方式）。

        当前端加密过于复杂无法逆向时，可以直接从浏览器复制 Cookie
        写入 credentials.json 的 "cookies" 字段，跳过登录流程。

        credentials.json 格式：
        {
            "cookies": {
                "session_id": "abc123",
                "token": "eyJhbGci..."
            }
        }
        或字符串格式（从浏览器 Cookie 编辑器复制）：
        {
            "cookies": "session_id=abc123; token=eyJhbGci..."
        }

        Args:
            creds: 凭证字典

        Returns:
            bool: 是否成功加载了 Cookie
        """
        cookies_data = creds.get('cookies')
        if not cookies_data:
            return False

        if isinstance(cookies_data, dict):
            for name, value in cookies_data.items():
                self.session.cookies.set(name, str(value))
            return True

        if isinstance(cookies_data, str):
            # 解析 "key1=val1; key2=val2" 格式
            for pair in cookies_data.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    name, value = pair.split('=', 1)
                    self.session.cookies.set(name.strip(), value.strip())
            return True

        return False

    def is_logged_in(self):
        # type: () -> bool
        """检查是否已登录"""
        return self._logged_in

    def close(self):
        """关闭会话"""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
