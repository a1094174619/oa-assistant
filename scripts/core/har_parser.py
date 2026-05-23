# -*- coding: utf-8 -*-
"""
HAR 文件解析器

解析 Chrome 导出的 HAR 文件，过滤静态资源，提取有效 HTTP 请求。
借鉴 har2requests 的 header 推断算法，增强 token 来源追踪。

兼容: Python 3.8+, Windows 7
"""
import json
import os
import sys
import argparse
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs, unquote

# 静态资源扩展名（过滤噪音）
STATIC_EXTENSIONS = frozenset([
    '.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
    '.woff', '.woff2', '.ttf', '.eot', '.otf', '.mp3', '.mp4',
    '.wav', '.avi', '.flv', '.swf', '.map', '.webp', '.bmp',
    '.cur', '.ani', '.rss', '.xml',  # xml 可能是 API，但多数是 sitemap
])

# 静态资源路径关键词
STATIC_PATH_KEYWORDS = frozenset([
    '/static/', '/assets/', '/dist/', '/build/', '/public/',
    '/images/', '/img/', '/fonts/', '/icons/', '/media/',
    '/bundle/', '/vendor/', '/node_modules/', '/webpack/',
])

# 需要过滤的 MIME 类型前缀
FILTERED_MIME_PREFIXES = (
    'image/', 'audio/', 'video/', 'font/',
    'text/css', 'application/javascript',
)


def _is_static_resource(entry):
    """判断请求是否为静态资源"""
    url = entry.get('request', {}).get('url', '')
    parsed = urlparse(url)
    path_lower = parsed.path.lower()

    # 检查扩展名
    for ext in STATIC_EXTENSIONS:
        if path_lower.endswith(ext):
            return True

    # 检查路径关键词
    for keyword in STATIC_PATH_KEYWORDS:
        if keyword in path_lower:
            return True

    # 检查响应 MIME 类型
    mime = entry.get('response', {}).get('content', {}).get('mimeType', '')
    if mime.startswith(FILTERED_MIME_PREFIXES):
        return True

    return False


def _is_options_request(entry):
    """判断是否为 CORS 预检请求"""
    return entry.get('request', {}).get('method', '').upper() == 'OPTIONS'


def _extract_headers(header_list):
    """从 HAR headers 数组转为字典"""
    if not header_list:
        return {}
    return {h['name']: h['value'] for h in header_list if 'name' in h and 'value' in h}


def _extract_query_params(url):
    """从 URL 提取 query 参数"""
    parsed = urlparse(url)
    params = {}
    for key, values in parse_qs(parsed.query).items():
        params[key] = values[0] if len(values) == 1 else values
    return params


def _extract_post_data(request):
    """提取 POST 请求体"""
    post_data = request.get('postData')
    if not post_data:
        return None

    mime = post_data.get('mimeType', '')
    text = post_data.get('text', '')

    result = {
        'mime_type': mime,
        'raw': text,
    }

    # 解析 JSON body
    if 'json' in mime or mime == 'application/json':
        try:
            result['json'] = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

    # 解析 form-urlencoded
    elif 'x-www-form-urlencoded' in mime:
        params = {}
        for pair in text.split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                params[unquote(k)] = unquote(v)
        result['form'] = params

    # 解析 HAR 的 params 字段
    params_list = post_data.get('params')
    if params_list:
        result['params'] = {p['name']: p.get('value', '') for p in params_list if 'name' in p}

    return result


def _extract_response_info(entry):
    """提取响应关键信息"""
    response = entry.get('response', {})
    content = response.get('content', {})
    headers = _extract_headers(response.get('headers', []))

    result = {
        'status': response.get('status', 0),
        'status_text': response.get('statusText', ''),
        'mime_type': content.get('mimeType', ''),
        'size': content.get('size', 0),
        'headers': headers,
    }

    # 提取响应体（可能很大，限制大小）
    text = content.get('text', '')
    if text and len(text) <= 102400:  # 100KB 以内保留
        result['body'] = text
        # 尝试解析 JSON
        if 'json' in result['mime_type']:
            try:
                result['body_json'] = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass

    # 提取 Set-Cookie
    cookies = response.get('cookies', [])
    if cookies:
        result['cookies'] = {c['name']: c.get('value', '') for c in cookies if 'name' in c}

    return result


def _extract_timing(entry):
    """提取请求时间信息"""
    started = entry.get('startedDateTime', '')
    time_ms = entry.get('time', 0)
    timings = entry.get('timings', {})

    return {
        'started_at': started,
        'duration_ms': time_ms,
        'blocked_ms': timings.get('blocked', -1),
        'dns_ms': timings.get('dns', -1),
        'connect_ms': timings.get('connect', -1),
        'send_ms': timings.get('send', 0),
        'wait_ms': timings.get('wait', 0),
        'receive_ms': timings.get('receive', 0),
    }


def parse_har(har_path):
    """
    解析 HAR 文件，返回过滤后的请求列表。

    Args:
        har_path: HAR 文件路径

    Returns:
        dict: {
            'meta': { 总请求数, 过滤后请求数, 域名列表 },
            'entries': [ 过滤后的请求条目 ]
        }
    """
    with open(har_path, 'r', encoding='utf-8') as f:
        har = json.load(f)

    log = har.get('log', {})
    raw_entries = log.get('entries', [])

    all_count = len(raw_entries)
    filtered_entries = []

    for idx, entry in enumerate(raw_entries):
        # 过滤静态资源和 OPTIONS 请求
        if _is_static_resource(entry):
            continue
        if _is_options_request(entry):
            continue

        request = entry.get('request', {})
        url = request.get('url', '')
        method = request.get('method', '').upper()

        if not url:
            continue

        parsed_url = urlparse(url)

        item = {
            'index': idx,  # 原始 HAR 中的序号
            'method': method,
            'url': url,
            'path': parsed_url.path or '/',
            'query': parsed_url.query,
            'host': parsed_url.netloc,
            'scheme': parsed_url.scheme,
            'query_params': _extract_query_params(url),
            'headers': _extract_headers(request.get('headers', [])),
            'cookies': {c['name']: c.get('value', '') for c in request.get('cookies', []) if 'name' in c},
            'post_data': _extract_post_data(request),
            'response': _extract_response_info(entry),
            'timing': _extract_timing(entry),
        }

        # 标记请求类型
        item['is_api'] = _is_api_request(item)
        item['is_document'] = _is_document_request(item)
        item['is_write'] = method in ('POST', 'PUT', 'DELETE', 'PATCH')

        filtered_entries.append(item)

    # 收集所有域名
    domains = sorted(set(e['host'] for e in filtered_entries if e['host']))

    # Header 推断：识别 session 级别 header 和动态 header 来源
    header_info = infer_header_origins(filtered_entries)

    # 为每个 entry 标注 header 来源信息
    header_sources = header_info.get('header_sources', {})
    session_headers = header_info.get('session_headers', [])
    for i, entry in enumerate(filtered_entries):
        entry['header_sources'] = header_sources.get(i, {})
        if i < len(session_headers):
            entry['session_headers'] = session_headers[i]
        else:
            entry['session_headers'] = {}

    return {
        'meta': {
            'total_requests': all_count,
            'filtered_requests': len(filtered_entries),
            'domains': domains,
            'source_file': har_path,
            'session_headers_base': session_headers[0] if session_headers else {},
            'header_dependency_count': len(header_sources),
            'password_encryption': detect_password_encryption(filtered_entries),
        },
        'entries': filtered_entries,
    }


def _is_api_request(item):
    """判断是否为 API 请求"""
    # XHR 标记
    if item['headers'].get('X-Requested-With') == 'XMLHttpRequest':
        return True
    # Accept 头包含 json
    accept = item['headers'].get('Accept', '')
    if 'application/json' in accept:
        return True
    # 路径包含 api
    if '/api/' in item['path'].lower():
        return True
    # 响应是 JSON
    if 'json' in item['response'].get('mime_type', ''):
        return True
    return False


def _is_document_request(item):
    """判断是否为页面请求（Document 类型）"""
    accept = item['headers'].get('Accept', '')
    if 'text/html' in accept and item['method'] == 'GET':
        return True
    mime = item['response'].get('mime_type', '')
    if 'text/html' in mime:
        return True
    return False


# ============================================================
# 密码加密特征检测
# ============================================================


def detect_password_encryption(entries):
    """
    检测登录请求中密码字段是否被前端加密。

    通过分析 HAR 中密码字段值的特征来判断：
    - MD5: 32 位 hex 字符串
    - SHA1: 40 位 hex 字符串
    - SHA256: 64 位 hex 字符串
    - Base64: 符合 base64 字符集且有 padding
    - RSA/AES: base64 编码、长度较长、非 human-readable
    - 明文: 短字符串、可读字符

    检测策略：
    1. 搜索登录类请求（path 含 login/auth，method 为 POST）
    2. 提取疑似密码字段的值
    3. 与常见加密模式匹配

    Args:
        entries: 解析后的请求列表

    Returns:
        dict: {
            'has_encryption': bool,
            'algo': str,           # 检测到的加密算法
            'encrypted_fields': [  # 被加密的字段
                {'field': 'password', 'raw_length': 5, 'encoded_length': 32, 'algo': 'MD5'}
            ],
            'warnings': [str],     # 警告信息
        }
    """
    import re

    login_keywords = ('login', 'auth', 'signin', 'sign-in', 'token', 'oauth')
    password_field_names = ('password', 'passwd', 'pwd', 'pass', 'userpwd',
                            'login_password', 'user_password', 'encrypted_password')

    for entry in entries:
        path_lower = entry.get('path', '').lower()
        method = entry.get('method', '')
        post_data = entry.get('post_data')

        # 检查是否为登录请求
        is_login = False
        for kw in login_keywords:
            if kw in path_lower:
                is_login = True
                break
        if not is_login:
            continue
        if method != 'POST':
            continue
        if not post_data:
            continue

        # 提取 POST body 字段
        body_fields = {}
        if 'json' in post_data and isinstance(post_data['json'], dict):
            body_fields = post_data['json']
        elif 'form' in post_data:
            body_fields = post_data['form']
        elif 'params' in post_data:
            body_fields = post_data['params']

        if not body_fields:
            continue

        # 查找密码字段并检测加密特征
        encrypted_fields = []
        seen_field_keys = set()
        for field_name in password_field_names:
            found_key = None
            for key in body_fields:
                if key in seen_field_keys:
                    continue
                if field_name in key.lower():
                    found_key = key
                    seen_field_keys.add(key)
                    break

            if not found_key:
                continue

            value = str(body_fields[found_key])
            if not value:
                continue

            algo = _detect_encryption_algo(value)
            if algo:
                encrypted_fields.append({
                    'field': found_key,
                    'raw_value_sample': value[:20] + ('...' if len(value) > 20 else ''),
                    'length': len(value),
                    'algo': algo,
                })

        if encrypted_fields:
            warnings = []
            for ef in encrypted_fields:
                warnings.append(
                    '密码字段 "{}" 疑似经由 {} 加密（长度 {}，样本: {}）'.format(
                        ef['field'], ef['algo'], ef['length'], ef['raw_value_sample'])
                )
            warnings.append(
                '登录接口需要实现 _encrypt_password() 方法，否则发送明文的 login() 将失败'
            )

            return {
                'has_encryption': True,
                'algo': encrypted_fields[0]['algo'],
                'encrypted_fields': encrypted_fields,
                'encryption_detected_in': entry.get('path', ''),
                'warnings': warnings,
            }

    return {
        'has_encryption': False,
        'algo': None,
        'encrypted_fields': [],
        'warnings': [],
    }


def _detect_encryption_algo(value):
    """
    检测单个值的加密算法。

    返回值：
        'MD5'     - 32位小写 hex
        'MD5_UP'  - 32位大写 hex
        'SHA1'    - 40位 hex
        'SHA256'  - 64位 hex
        'SM3'     - 64位 hex（与 SHA256 不可区分，标记需人工确认）
        'Base64'  - base64 编码
        'RSA_HEX' - 长 hex 字符串（疑似 RSA）
        'Custom'  - 有明显加密特征但无法确定算法
        None      - 疑似明文或规则不足
    """
    if not value or not isinstance(value, str):
        return None

    length = len(value)

    # MD5: 32 位 hex（小写）
    if length == 32 and _is_hex(value):
        if value.islower() or value == value.lower():
            return 'MD5'
        return 'MD5_UP'

    # SHA1: 40 位 hex
    if length == 40 and _is_hex(value):
        return 'SHA1'

    # SHA256/SM3: 64 位 hex
    if length == 64 and _is_hex(value):
        return 'SHA256'

    # 长 hex 字符串（256-1024 位 = 64-256 字符 hex）
    if length >= 128 and length % 2 == 0 and _is_hex(value):
        return 'RSA_HEX'

    # Base64 编码
    if _is_base64(value):
        if length >= 64:
            return 'RSA/Base64'
        return 'Base64'

    # 疑似自定义加密：长度较长且看起来不像自然语言
    # 阈值：长度>=16，且包含大小写+数字混合（纯小写+数字如 hello123 是明文）
    if length >= 16 and _looks_encrypted(value):
        return 'Custom'

    return None


def _is_hex(value):
    """检查字符串是否全部由 0-9a-fA-F 组成"""
    import re
    return bool(re.match(r'^[0-9a-fA-F]+$', value))


def _is_base64(value):
    """检查字符串是否符合 base64 编码模式"""
    import re
    if not re.match(r'^[A-Za-z0-9+/]+=*$', value):
        return False
    if len(value) % 4 != 0:
        return False
    # 长度太短不判定为 base64（如 "test" 也符合）
    return len(value) >= 16


def _looks_encrypted(value):
    """判断字符串是否看起来像加密过的（大小写+数字混合，非自然语言）"""
    import re
    # 纯小写+数字（如 hello123）是明文
    if value.islower() or value.isupper():
        return False
    # 必须同时包含大小写字母和数字
    has_upper = bool(re.search(r'[A-Z]', value))
    has_lower = bool(re.search(r'[a-z]', value))
    has_digit = bool(re.search(r'[0-9]', value))
    if has_upper and has_lower and has_digit:
        return True
    # 包含 /+= 等编码符号
    if re.match(r'^[A-Za-z0-9/+=]+$', value) and len(value) >= 16:
        return True
    return False


# ============================================================
# Header 推断算法（借鉴 har2requests）
# ============================================================

# header 值最小搜索长度（太短的值不搜索）
_HEADER_SEARCH_MIN_LEN = 16

# 向前搜索响应的最大数量
_RESPONSE_LOOKUP_DEPTH = 5

# 响应体最大搜索大小
_MAX_RESPONSE_SEARCH_SIZE = 100000


def infer_session_headers(entries):
    """
    推断哪些 header 在所有请求中通用（应设为 session 级别）。

    借鉴 har2requests 的 infer_session_headers 算法：
    - 统计每个 header key 出现的频率
    - 如果某个 key 的某个 value 出现频率 > 50%，认为是 session header
    - 跟踪 session header 的变化（如 token 更新）

    Args:
        entries: 解析后的请求列表

    Returns:
        list[dict]: 每个 entry 对应的 session headers 快照
    """
    from collections import Counter

    n = len(entries)
    if n == 0:
        return []

    count = Counter()
    record = [[] for _ in range(n)]

    for i in range(n - 1, -1, -1):
        r = entries[i]
        for k, v in r.get('headers', {}).items():
            count[k] += 1
            count[(k, v)] += 1
            if count[(k, v)] > 1 and count[(k, v)] / count[k] > 0.5:
                record[i].append(k)

    result = []
    current_headers = {}
    for i in range(n):
        r = entries[i]
        for k in record[i]:
            if k in r.get('headers', {}):
                current_headers[k] = r['headers'][k]

        to_remove = []
        for k in current_headers:
            if k in r.get('headers', {}):
                count[k] -= 1
            elif count[k] / max(n - i, 1) < 0.5:
                to_remove.append(k)
        for k in to_remove:
            del current_headers[k]

        result.append(dict(current_headers))

    return result


def infer_header_origins(entries):
    """
    推断动态 header 值的来源（如 token 来自哪个响应）。

    借鉴 har2requests 的 infer_headers_origin 算法：
    - 对于非常量 header（如 Authorization），搜索前面响应的 body
    - 如果 header 值的子串出现在某个响应中，建立依赖关系

    Args:
        entries: 解析后的请求列表

    Returns:
        dict: {
            'header_sources': { entry_index: { header_key: source_entry_index } },
            'session_headers': list[dict],
        }
    """
    session_headers_list = infer_session_headers(entries)
    if not session_headers_list:
        return {'header_sources': {}, 'session_headers': []}

    base_headers = session_headers_list[0] if session_headers_list else {}
    header_sources = {}

    response_db = []

    for i, entry in enumerate(entries):
        entry_headers = entry.get('headers', {})
        session_h = session_headers_list[i] if i < len(session_headers_list) else {}

        for header_key, value in entry_headers.items():
            if header_key in session_h:
                continue
            if len(value) < _HEADER_SEARCH_MIN_LEN:
                continue

            # 搜索前面响应的文本（body + headers）
            for resp_idx, resp_text in reversed(response_db[-_RESPONSE_LOOKUP_DEPTH:]):
                if not resp_text or len(resp_text) > _MAX_RESPONSE_SEARCH_SIZE:
                    continue
                # 精确匹配
                if value in resp_text:
                    if i not in header_sources:
                        header_sources[i] = {}
                    header_sources[i][header_key] = resp_idx
                    break
                # 子串匹配：检查 value 的长后缀（如去掉 "Bearer " 前缀）
                found = False
                search_value = value
                while len(search_value) >= _HEADER_SEARCH_MIN_LEN:
                    if search_value in resp_text:
                        if i not in header_sources:
                            header_sources[i] = {}
                        header_sources[i][header_key] = resp_idx
                        found = True
                        break
                    # 去掉第一个空格前的部分（如 "Bearer " → "eyJ..."）
                    space_idx = search_value.find(' ')
                    if space_idx < 0:
                        break
                    search_value = search_value[space_idx + 1:]
                if found:
                    break

        # 收集响应的可搜索文本：body + response headers + Set-Cookie
        resp = entry.get('response', {})
        resp_parts = []

        resp_body = resp.get('body', '')
        if resp_body:
            resp_parts.append(resp_body)

        # 响应 header 值（如 Location、Set-Cookie 中的 token）
        for h_key, h_val in resp.get('headers', {}).items():
            if h_val and len(h_val) >= _HEADER_SEARCH_MIN_LEN:
                resp_parts.append(h_val)

        # Set-Cookie 值
        for c_val in resp.get('cookies', {}).values():
            if c_val and len(c_val) >= _HEADER_SEARCH_MIN_LEN:
                resp_parts.append(c_val)

        resp_text_combined = '\n'.join(resp_parts)
        if resp_text_combined and len(resp_text_combined) <= _MAX_RESPONSE_SEARCH_SIZE:
            response_db.append((i, resp_text_combined))

    return {
        'header_sources': header_sources,
        'session_headers': session_headers_list,
    }


def main():
    parser = argparse.ArgumentParser(description='解析 HAR 文件，过滤静态资源')
    parser.add_argument('har_file', help='HAR 文件路径')
    parser.add_argument('-o', '--output', help='输出 JSON 文件路径')
    parser.add_argument('--all', action='store_true', help='不过滤，输出全部请求')
    args = parser.parse_args()

    # 支持相对路径：基于 cwd 解析
    har_path = os.path.abspath(args.har_file)
    result = parse_har(har_path)

    if args.all:
        # 重新解析不过滤
        with open(args.har_file, 'r', encoding='utf-8') as f:
            har = json.load(f)
        count = len(har.get('log', {}).get('entries', []))
        print("[INFO] 总请求数: {}".format(count))

    print("[INFO] 过滤后请求数: {}".format(result['meta']['filtered_requests']))
    print("[INFO] 涉及域名: {}".format(', '.join(result['meta']['domains'])))

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("[OK] 已保存至: {}".format(args.output))
    else:
        # 输出摘要
        for entry in result['entries']:
            flag = '★' if entry['is_write'] else ' '
            api_flag = '[API]' if entry['is_api'] else '[DOC]' if entry['is_document'] else '[???]'
            print("  {} {} {} {}{}".format(
                flag, entry['method'].ljust(6), api_flag.ljust(6),
                entry['path'], ' ?' + entry['query'] if entry['query'] else ''
            ))


if __name__ == '__main__':
    main()
