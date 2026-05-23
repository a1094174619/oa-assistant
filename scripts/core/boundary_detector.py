# -*- coding: utf-8 -*-
"""
操作边界检测器

从过滤后的请求序列中识别业务操作的边界，将请求分组。

策略优先级：
1. 写操作（POST/PUT/DELETE/PATCH）→ 新操作边界
2. URL 路径变化 → 可能是新操作
3. 同 URL 参数变化 → 参数化操作（如翻页）
4. 时序间隔 → 辅助判断
5. 响应依赖 → 合并附属请求

兼容: Python 3.8+, Windows 7
"""
import json
import argparse
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse

# 写操作间隔超过此阈值（秒），认为是不同的用户操作
TIME_GAP_THRESHOLD = 3.0

# 同一操作组内的附属 GET 请求最大数量
MAX_ATTACHED_GETS = 3


def _parse_iso_time(time_str):
    """解析 ISO 8601 时间字符串，返回时间戳（秒）"""
    if not time_str:
        return 0.0
    try:
        # Chrome HAR 格式: 2026-05-21T10:30:00.123Z
        from datetime import datetime, timezone
        # Python 3.8 兼容：手动解析
        time_str = time_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(time_str)
        return dt.timestamp()
    except Exception:
        return 0.0


def _time_gap(entry_a, entry_b):
    """计算两个请求之间的时间间隔（秒）"""
    ta = _parse_iso_time(entry_a.get('timing', {}).get('started_at', ''))
    tb = _parse_iso_time(entry_b.get('timing', {}).get('started_at', ''))
    if ta == 0.0 or tb == 0.0:
        return 0.0
    return abs(tb - ta)


def _is_same_path_group(path_a, path_b):
    """
    判断两个路径是否属于同一组（忽略路径末尾的 ID 段）。

    /api/mail/list 和 /api/mail/list → 同组
    /api/mail/detail/123 和 /api/mail/detail/456 → 同组
    /api/mail/send 和 /api/mail/forward → 不同组
    """
    parts_a = [p for p in path_a.strip('/').split('/') if p]
    parts_b = [p for p in path_b.strip('/').split('/') if p]

    if len(parts_a) != len(parts_b):
        return False

    for pa, pb in zip(parts_a, parts_b):
        # 如果某一段都是数字，认为是 ID，跳过比较
        if pa.isdigit() and pb.isdigit():
            continue
        # 如果某一段看起来像 UUID 或 hash，跳过
        if _looks_like_id(pa) and _looks_like_id(pb):
            continue
        if pa.lower() != pb.lower():
            return False

    return True


def _looks_like_id(s):
    """判断字符串是否看起来像 ID（数字、UUID、长hash）"""
    if s.isdigit():
        return True
    if len(s) >= 8 and '-' in s:
        return True  # UUID-like
    if len(s) >= 16 and s.isalnum():
        return True  # hash-like
    return False


def _extract_path_id(path):
    """提取路径末尾的 ID 段"""
    parts = [p for p in path.strip('/').split('/') if p]
    if parts and _looks_like_id(parts[-1]):
        return parts[-1]
    return None


def _has_response_dependency(entry, prev_entries):
    """
    检查当前请求是否依赖前面某个请求的响应。

    判断依据：
    - 请求 header 中的 token 来自**紧邻的前一个**响应
    - 请求 URL 中的 ID 来自前一个响应的 body

    注意：只检查紧邻的前一个请求，避免所有带 token 的请求都被合并。
    """
    if not prev_entries:
        return False

    # 只检查紧邻的前一个请求
    prev = prev_entries[-1]
    prev_body = prev.get('response', {}).get('body_json', {})

    if not isinstance(prev_body, dict):
        return False

    # 检查当前请求是否使用了前一个响应中的 token
    # 且前一个请求必须是写操作（POST/PUT/DELETE）
    if not prev.get('is_write'):
        return False

    auth_header = entry.get('headers', {}).get('Authorization', '')
    if auth_header and 'token' in prev_body:
        prev_token = prev_body.get('token', '')
        if prev_token and prev_token in auth_header:
            return True

    return False


def detect_boundaries(parsed_data):
    """
    从解析后的请求数据中检测操作边界，返回操作组。

    Args:
        parsed_data: har_parser.parse_har() 的输出

    Returns:
        dict: {
            'meta': { 操作组数量, 使用的策略 },
            'groups': [ 操作组列表 ]
        }
    """
    entries = parsed_data.get('entries', [])
    if not entries:
        return {'meta': {'group_count': 0, 'strategies': []}, 'groups': []}

    groups = []
    current_group = None
    attached_get_count = 0
    strategies_used = set()

    for i, entry in enumerate(entries):
        should_start_new = False
        strategy = ''

        # 策略 5（优先）: 响应依赖 → 不开新组，合并到当前组
        # 只合并写操作后紧邻的第一个 GET（如 login 后的 get_user_info）
        # 避免把所有带 token 的请求都合并进来
        if current_group is not None and attached_get_count == 0:
            primary_entry = current_group.get('primary', {})
            if _has_response_dependency(entry, [primary_entry]):
                should_start_new = False
                strategy = 'response_dependency'
                # 直接作为附属请求
                current_group['attached'].append(entry)
                attached_get_count += 1
                strategies_used.add(strategy)
                continue

        # 策略 1: 写操作 → 新操作边界
        if entry.get('is_write'):
            if current_group is not None:
                # 如果当前组的主请求也是写操作，先保存
                if current_group.get('primary', {}).get('is_write'):
                    groups.append(current_group)
                    current_group = None
                    attached_get_count = 0
            should_start_new = True
            strategy = 'write_operation'

        # 策略 2: URL 路径显著变化
        elif current_group is not None:
            primary_path = current_group.get('primary', {}).get('path', '')
            current_path = entry.get('path', '')

            if primary_path and current_path:
                if not _is_same_path_group(primary_path, current_path):
                    # 路径不同，可能是新操作
                    if entry.get('is_api') or entry.get('is_document'):
                        should_start_new = True
                        strategy = 'path_change'

        # 策略 3: 时序间隔
        if not should_start_new and current_group is not None and i > 0:
            gap = _time_gap(entries[i - 1], entry)
            if gap > TIME_GAP_THRESHOLD:
                # 如果间隔大，且当前是 API 请求，可能是新操作
                if entry.get('is_api') and not entry.get('is_write'):
                    should_start_new = True
                    strategy = 'time_gap'

        # 执行分组
        if should_start_new:
            # 保存当前组
            if current_group is not None:
                groups.append(current_group)

            current_group = {
                'primary': entry,
                'attached': [],
                'strategy': strategy,
            }
            attached_get_count = 0
            strategies_used.add(strategy)
        else:
            # 附属请求
            if current_group is None:
                # 没有主请求，自己作为主请求
                current_group = {
                    'primary': entry,
                    'attached': [],
                    'strategy': 'first_entry',
                }
                attached_get_count = 0
            else:
                # 限制附属请求数量，防止无限合并
                if attached_get_count < MAX_ATTACHED_GETS:
                    current_group['attached'].append(entry)
                    attached_get_count += 1
                else:
                    # 超出限制，作为新组
                    groups.append(current_group)
                    current_group = {
                        'primary': entry,
                        'attached': [],
                        'strategy': 'overflow',
                    }
                    attached_get_count = 0

    # 保存最后一组
    if current_group is not None:
        groups.append(current_group)

    # 为每组生成摘要
    for idx, group in enumerate(groups):
        group['group_index'] = idx
        group['summary'] = _summarize_group(group)

    return {
        'meta': {
            'group_count': len(groups),
            'strategies': sorted(strategies_used),
            'total_entries': len(entries),
        },
        'groups': groups,
    }


def _summarize_group(group):
    """生成操作组的摘要信息"""
    primary = group.get('primary', {})
    method = primary.get('method', '?')
    path = primary.get('path', '?')
    attached_count = len(group.get('attached', []))
    is_write = primary.get('is_write', False)

    return {
        'method': method,
        'path': path,
        'is_write': is_write,
        'attached_count': attached_count,
        'host': primary.get('host', ''),
        'has_post_data': primary.get('post_data') is not None,
    }


def main():
    parser = argparse.ArgumentParser(description='检测 HTTP 请求的操作边界')
    parser.add_argument('input', help='har_parser 输出的 JSON 文件')
    parser.add_argument('-o', '--output', help='输出 JSON 文件路径')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        parsed_data = json.load(f)

    result = detect_boundaries(parsed_data)

    print("[INFO] 操作组数量: {}".format(result['meta']['group_count']))
    for group in result['groups']:
        s = group['summary']
        flag = '★' if s['is_write'] else ' '
        attached = ' (+{} GET)'.format(s['attached_count']) if s['attached_count'] else ''
        print("  {} Group {}: {} {}{}".format(
            flag, group['group_index'], s['method'].ljust(6), s['path'], attached
        ))

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("[OK] 已保存至: {}".format(args.output))


if __name__ == '__main__':
    main()
