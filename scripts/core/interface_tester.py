# -*- coding: utf-8 -*-
"""
接口测试器 - 三级闭环测试框架

L1 静态检查：语法、导入、方法签名（零成本）
L2 Dry-Run：构造请求但不发送，验证 URL/参数/header 组装（零网络）
L3 Live-Test：真实发送请求，验证响应结构和状态码（需凭证）

诊断报告：结构化 JSON，包含错误类型、上下文、修复建议

兼容: Python 3.8+, Windows 7
"""
import ast
import importlib
import json
import os
import sys
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple


# ============================================================
# L1: 静态检查
# ============================================================

def l1_static_check(interface_path, site_config_path=None):
    """
    L1 静态检查：不执行代码，仅通过 AST 分析和导入测试验证。

    检查项：
    1. Python 语法正确性
    2. 导入语句可解析
    3. 类定义存在且包含预期方法
    4. 方法签名完整性（参数与 site_config 一致）
    5. _request 方法存在
    6. login 方法存在

    Args:
        interface_path: interface.py 文件路径
        site_config_path: site_config.json 文件路径（可选，用于交叉验证）

    Returns:
        dict: {
            'level': 'L1',
            'passed': bool,
            'checks': [ { 'name': str, 'passed': bool, 'detail': str } ],
            'errors': [ { 'type': str, 'message': str, 'fix_hint': str } ],
        }
    """
    checks = []
    errors = []

    # 1. 语法检查
    check = {'name': 'syntax', 'passed': False, 'detail': ''}
    try:
        with open(interface_path, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source)
        check['passed'] = True
        check['detail'] = '语法正确'
    except SyntaxError as e:
        check['detail'] = '语法错误: 行 {} - {}'.format(e.lineno, e.msg)
        errors.append({
            'type': 'syntax_error',
            'message': check['detail'],
            'fix_hint': '修复第 {} 行的语法错误'.format(e.lineno),
            'line': e.lineno,
        })
    checks.append(check)

    if not check['passed']:
        return _build_result('L1', False, checks, errors)

    # 2. 类定义检查
    check = {'name': 'class_definition', 'passed': False, 'detail': ''}
    class_defs = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if class_defs:
        class_name = class_defs[0].name
        check['passed'] = True
        check['detail'] = '找到类定义: {}'.format(class_name)
    else:
        check['detail'] = '未找到类定义'
        errors.append({
            'type': 'missing_class',
            'message': 'interface.py 中没有类定义',
            'fix_hint': '确保代码生成器输出了类定义',
        })
    checks.append(check)

    if not check['passed']:
        return _build_result('L1', False, checks, errors)

    # 3. 方法列表检查
    check = {'name': 'methods', 'passed': False, 'detail': ''}
    class_node = class_defs[0]
    methods = [n.name for n in class_node.body if isinstance(n, ast.FunctionDef)]
    expected_methods = {'_request', 'login'}

    missing = expected_methods - set(methods)
    if not missing:
        check['passed'] = True
        check['detail'] = '方法列表: {}'.format(', '.join(methods))
    else:
        check['detail'] = '缺少方法: {}'.format(', '.join(missing))
        errors.append({
            'type': 'missing_method',
            'message': '缺少必要方法: {}'.format(', '.join(missing)),
            'fix_hint': '确保代码生成器输出了 _request 和 login 方法',
        })
    checks.append(check)

    # 4. 与 site_config 交叉验证
    if site_config_path and os.path.exists(site_config_path):
        check = {'name': 'config_consistency', 'passed': False, 'detail': ''}
        try:
            with open(site_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config_methods = {op['name'] for op in config.get('operations', [])}
            code_methods = set(methods) - {'__init__', '_request'}

            missing_in_code = config_methods - code_methods
            extra_in_code = code_methods - config_methods

            if not missing_in_code:
                check['passed'] = True
                detail_parts = ['配置与代码一致']
                if extra_in_code:
                    detail_parts.append('代码多出: {}'.format(', '.join(extra_in_code)))
                check['detail'] = '; '.join(detail_parts)
            else:
                check['detail'] = '代码缺少: {}'.format(', '.join(missing_in_code))
                errors.append({
                    'type': 'method_mismatch',
                    'message': 'site_config 中定义但代码中缺少的方法: {}'.format(
                        ', '.join(missing_in_code)),
                    'fix_hint': '检查代码生成器是否遗漏了某些操作',
                })
        except Exception as e:
            check['detail'] = '无法读取 site_config: {}'.format(e)
        checks.append(check)

    # 5. 导入检查（尝试 import 但不执行业务逻辑）
    check = {'name': 'imports', 'passed': False, 'detail': ''}
    try:
        import_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                import_names.add(node.module or '')

        # 检查 requests 是否导入
        if 'requests' in import_names:
            check['passed'] = True
            check['detail'] = '导入检查通过'
        else:
            check['detail'] = '缺少 requests 导入'
            errors.append({
                'type': 'missing_import',
                'message': '缺少 import requests',
                'fix_hint': '添加 import requests',
            })
    except Exception as e:
        check['detail'] = '导入分析失败: {}'.format(e)
    checks.append(check)

    # 6. 方法签名检查（每个方法至少有 self 参数）
    check = {'name': 'method_signatures', 'passed': False, 'detail': ''}
    bad_sigs = []
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef):
            args = [a.arg for a in node.args.args]
            if not args or args[0] != 'self':
                bad_sigs.append(node.name)
    if not bad_sigs:
        check['passed'] = True
        check['detail'] = '所有方法签名正确'
    else:
        check['detail'] = '签名异常方法: {}'.format(', '.join(bad_sigs))
        errors.append({
            'type': 'bad_signature',
            'message': '方法缺少 self 参数: {}'.format(', '.join(bad_sigs)),
            'fix_hint': '修复方法签名，确保第一个参数为 self',
        })
    checks.append(check)

    passed = all(c['passed'] for c in checks)
    return _build_result('L1', passed, checks, errors)


# ============================================================
# L2: Dry-Run 测试
# ============================================================

def l2_dry_run(interface_path, site_id, base_dir):
    """
    L2 Dry-Run 测试：实例化类，调用方法但不发送请求。

    通过 mock requests.Session 来拦截请求，验证：
    1. 类可以正确实例化
    2. login 方法可以调用（不发送请求）
    3. 各方法的 URL 构造正确
    4. 参数传递正确

    Args:
        interface_path: interface.py 文件路径
        site_id: 站点标识
        base_dir: skill 根目录

    Returns:
        dict: {
            'level': 'L2',
            'passed': bool,
            'checks': [ ... ],
            'errors': [ ... ],
            'captured_requests': [ { 'method', 'url', 'kwargs' } ],
        }
    """
    checks = []
    errors = []
    captured_requests = []

    # 动态导入 interface 模块
    check = {'name': 'module_load', 'passed': False, 'detail': ''}
    module = None
    try:
        module_dir = os.path.dirname(os.path.abspath(interface_path))
        module_name = os.path.splitext(os.path.basename(interface_path))[0]
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)
        # 清除旧缓存
        for key in list(sys.modules.keys()):
            if key == module_name or key.startswith(module_name + '.'):
                del sys.modules[key]
        module = importlib.import_module(module_name)
        check['passed'] = True
        check['detail'] = '模块加载成功: {}'.format(module_name)
    except Exception as e:
        check['detail'] = '模块加载失败: {}'.format(e)
        errors.append({
            'type': 'import_error',
            'message': str(e),
            'fix_hint': '检查 interface.py 的导入语句和语法',
        })
    checks.append(check)

    if not check['passed']:
        return _build_result('L2', False, checks, errors,
                             captured_requests=captured_requests)

    # 找到类
    check = {'name': 'class_instantiation', 'passed': False, 'detail': ''}
    instance = None
    class_name = None
    try:
        for name in dir(module):
            obj = getattr(module, name)
            if (isinstance(obj, type)
                    and name not in ('object', 'type', 'BaseException', 'Any',
                                     'Dict', 'List', 'Optional', 'Tuple', 'Union')
                    and not name.startswith('_')):
                class_name = name
                instance = obj(base_url='http://dry-run-test.local')
                break
        if instance is None:
            check['detail'] = '未找到可实例化的类'
            errors.append({
                'type': 'no_class',
                'message': '模块中没有找到类定义',
                'fix_hint': '确保 interface.py 中定义了类',
            })
        else:
            check['passed'] = True
            check['detail'] = '实例化成功: {}()'.format(class_name)
    except Exception as e:
        check['detail'] = '实例化失败: {}'.format(e)
        errors.append({
            'type': 'instantiation_error',
            'message': str(e),
            'fix_hint': '检查 __init__ 方法是否需要额外参数',
        })
    checks.append(check)

    if not check['passed']:
        return _build_result('L2', False, checks, errors,
                             captured_requests=captured_requests)

    # Mock session 来拦截请求
    check = {'name': 'request_intercept', 'passed': False, 'detail': ''}
    try:
        original_request = instance.session.request

        def mock_request(method, url, **kwargs):
            captured_requests.append({
                'method': method,
                'url': url,
                'kwargs': {
                    'params': kwargs.get('params'),
                    'json': kwargs.get('json'),
                    'data': kwargs.get('data'),
                    'headers': kwargs.get('headers'),
                },
            })
            # 返回一个 mock 响应
            class MockResponse(object):
                status_code = 200
                text = '{"code": 0, "msg": "ok", "data": {}}'
                def json(self):
                    return json.loads(self.text)
                def raise_for_status(self):
                    pass
            return MockResponse()

        instance.session.request = mock_request
        check['passed'] = True
        check['detail'] = '请求拦截器安装成功'
    except Exception as e:
        check['detail'] = '拦截器安装失败: {}'.format(e)
        errors.append({
            'type': 'mock_error',
            'message': str(e),
            'fix_hint': '检查 session 对象是否正确初始化',
        })
    checks.append(check)

    if not check['passed']:
        return _build_result('L2', False, checks, errors,
                             captured_requests=captured_requests)

    # 测试 login 方法
    check = {'name': 'login_method', 'passed': False, 'detail': ''}
    try:
        result = instance.login('test_user', 'test_pass')
        if captured_requests:
            req = captured_requests[-1]
            check['passed'] = True
            check['detail'] = 'login → {} {}'.format(req['method'], req['url'])
        else:
            check['detail'] = 'login 未产生请求'
            errors.append({
                'type': 'no_request',
                'message': 'login 方法调用后没有产生 HTTP 请求',
                'fix_hint': '检查 login 方法是否正确调用了 _request',
            })
    except Exception as e:
        check['detail'] = 'login 调用失败: {}'.format(e)
        errors.append({
            'type': 'login_error',
            'message': str(e),
            'fix_hint': '检查 login 方法的参数和实现',
            'traceback': traceback.format_exc(),
        })
    checks.append(check)

    # 测试其他方法
    methods = [m for m in dir(instance) if not m.startswith('_') and m != 'login'
               and callable(getattr(instance, m))]

    for method_name in methods:
        check = {'name': 'method_{}'.format(method_name), 'passed': False, 'detail': ''}
        try:
            method = getattr(instance, method_name)
            # 尝试获取方法签名来构造参数
            import inspect
            try:
                sig = inspect.signature(method)
            except (ValueError, TypeError):
                sig = None

            # 构造测试参数：str→'test', int→1, 其他→'test'
            test_args = []
            if sig:
                for param_name, param in sig.parameters.items():
                    if param_name == 'self':
                        continue
                    if param.default is not inspect.Parameter.empty:
                        test_args.append(param.default)
                    elif param.annotation == 'int':
                        test_args.append(1)
                    else:
                        test_args.append('test')

            result = method(*test_args)
            if captured_requests:
                req = captured_requests[-1]
                check['passed'] = True
                # 验证 URL 格式
                url = req['url']
                if 'dry-run-test.local' in url:
                    check['detail'] = '{} → {} {}'.format(method_name, req['method'], url)
                else:
                    check['detail'] = '{} → URL 未使用 base_url: {}'.format(method_name, url)
                    errors.append({
                        'type': 'url_error',
                        'message': '{} 方法的 URL 未拼接 base_url: {}'.format(method_name, url),
                        'fix_hint': '确保方法使用 self.base_url + path 构造 URL',
                        'method': method_name,
                    })
                    check['passed'] = False
            else:
                check['detail'] = '{} 未产生请求'.format(method_name)
        except TypeError as e:
            check['detail'] = '{} 参数错误: {}'.format(method_name, e)
            errors.append({
                'type': 'param_error',
                'message': '{} 方法参数不匹配: {}'.format(method_name, e),
                'fix_hint': '检查方法签名与调用参数是否一致',
                'method': method_name,
            })
        except Exception as e:
            check['detail'] = '{} 调用异常: {}'.format(method_name, e)
            errors.append({
                'type': 'method_error',
                'message': '{}: {}'.format(method_name, e),
                'fix_hint': '检查方法实现',
                'method': method_name,
                'traceback': traceback.format_exc(),
            })
        checks.append(check)

    passed = all(c['passed'] for c in checks)
    return _build_result('L2', passed, checks, errors,
                         captured_requests=captured_requests)


# ============================================================
# L3: Live-Test 真实测试
# ============================================================

def l3_live_test(interface_path, site_id, base_url, credentials,
                 test_plan=None, base_dir=None):
    """
    L3 Live-Test：真实发送请求，验证接口可用性。

    Args:
        interface_path: interface.py 文件路径
        site_id: 站点标识
        base_url: 真实的基础 URL
        credentials: 登录凭证 {'username': ..., 'password': ...}
        test_plan: 测试计划（可选），指定每个方法的测试参数
            {'login': {'username': '...', 'password': '...'},
             'get_list_mail': {'page': 1}, ...}
        base_dir: skill 根目录

    Returns:
        dict: {
            'level': 'L3',
            'passed': bool,
            'checks': [ ... ],
            'errors': [ ... ],
            'responses': [ { 'method', 'url', 'status_code', 'response_keys' } ],
        }
    """
    checks = []
    errors = []
    responses = []

    # 动态导入
    check = {'name': 'module_load', 'passed': False, 'detail': ''}
    module = None
    try:
        module_dir = os.path.dirname(os.path.abspath(interface_path))
        module_name = os.path.splitext(os.path.basename(interface_path))[0]
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)
        # 清除旧缓存
        for key in list(sys.modules.keys()):
            if key == module_name or key.startswith(module_name + '.'):
                del sys.modules[key]
        module = importlib.import_module(module_name)
        check['passed'] = True
        check['detail'] = '模块加载成功: {}'.format(module_name)
    except Exception as e:
        check['detail'] = '模块加载失败: {}'.format(e)
        errors.append({
            'type': 'import_error',
            'message': str(e),
            'fix_hint': '检查 interface.py 的导入语句和语法',
        })
    checks.append(check)

    if not check['passed']:
        return _build_result('L3', False, checks, errors, responses=responses)

    # 实例化
    check = {'name': 'class_instantiation', 'passed': False, 'detail': ''}
    instance = None
    try:
        for name in dir(module):
            obj = getattr(module, name)
            if (isinstance(obj, type)
                    and name not in ('object', 'type', 'BaseException', 'Any',
                                     'Dict', 'List', 'Optional', 'Tuple', 'Union')
                    and not name.startswith('_')):
                instance = obj(base_url=base_url)
                break
        if instance:
            check['passed'] = True
            check['detail'] = '实例化成功'
        else:
            check['detail'] = '未找到类'
    except Exception as e:
        check['detail'] = '实例化失败: {}'.format(e)
    checks.append(check)

    if not check['passed']:
        return _build_result('L3', False, checks, errors, responses=responses)

    # 登录
    check = {'name': 'login', 'passed': False, 'detail': ''}
    login_result = None
    try:
        login_kwargs = {}
        if test_plan and 'login' in test_plan:
            login_kwargs = test_plan['login']
        else:
            login_kwargs = credentials

        login_result = instance.login(**login_kwargs)
        check['passed'] = True
        check['detail'] = '登录成功'

        responses.append({
            'method': 'login',
            'status': 'success',
            'response_keys': sorted(login_result.keys()) if isinstance(login_result, dict) else [],
            'response_sample': _safe_sample(login_result),
        })
    except Exception as e:
        check['detail'] = '登录失败: {}'.format(e)
        errors.append({
            'type': 'login_failed',
            'message': str(e),
            'fix_hint': _diagnose_login_error(e, instance, login_kwargs),
            'traceback': traceback.format_exc(),
        })
    checks.append(check)

    if not check['passed']:
        return _build_result('L3', False, checks, errors, responses=responses)

    # 测试其他方法
    methods = [m for m in dir(instance) if not m.startswith('_') and m != 'login'
               and callable(getattr(instance, m))]

    for method_name in methods:
        check = {'name': 'method_{}'.format(method_name), 'passed': False, 'detail': ''}
        try:
            method = getattr(instance, method_name)

            # 构造参数
            kwargs = {}
            if test_plan and method_name in test_plan:
                kwargs = test_plan[method_name]
            else:
                # 自动构造：从方法签名推断
                import inspect
                try:
                    sig = inspect.signature(method)
                    for param_name, param in sig.parameters.items():
                        if param_name == 'self':
                            continue
                        if param.default is not inspect.Parameter.empty:
                            continue  # 使用默认值
                        if param.annotation == 'int':
                            kwargs[param_name] = 1
                        else:
                            kwargs[param_name] = 'test_value'
                except (ValueError, TypeError):
                    pass

            result = method(**kwargs)
            check['passed'] = True
            check['detail'] = '调用成功'

            responses.append({
                'method': method_name,
                'status': 'success',
                'response_keys': sorted(result.keys()) if isinstance(result, dict) else [],
                'response_sample': _safe_sample(result),
            })
        except Exception as e:
            tb = traceback.format_exc()
            check['detail'] = '调用失败: {}'.format(e)
            errors.append({
                'type': 'method_failed',
                'message': '{}: {}'.format(method_name, e),
                'fix_hint': _diagnose_method_error(e, method_name, kwargs),
                'method': method_name,
                'kwargs': kwargs,
                'traceback': tb,
            })
            responses.append({
                'method': method_name,
                'status': 'failed',
                'error': str(e),
            })
        checks.append(check)

    passed = all(c['passed'] for c in checks)
    return _build_result('L3', passed, checks, errors, responses=responses)


# ============================================================
# 诊断辅助
# ============================================================

def _diagnose_login_error(error, instance, credentials):
    """诊断登录失败原因"""
    hints = []

    if isinstance(error, Exception):
        err_str = str(error).lower()

        if 'connection' in err_str or 'refused' in err_str:
            hints.append('无法连接服务器，检查 base_url 是否正确')
        elif '401' in err_str or 'unauthorized' in err_str:
            hints.append('认证失败，检查用户名密码是否正确')
        elif '403' in err_str or 'forbidden' in err_str:
            hints.append('被禁止访问，可能需要 CSRF token 或其他认证头')
        elif '404' in err_str or 'not found' in err_str:
            hints.append('登录接口路径错误，检查 /api/auth/login 是否正确')
        elif '422' in err_str or 'unprocessable' in err_str:
            hints.append('参数格式错误，可能需要 form 而非 json，或字段名不对')
        elif '500' in err_str or 'internal' in err_str:
            hints.append('服务器内部错误，可能是请求格式不兼容')
        elif 'timeout' in err_str:
            hints.append('请求超时，检查网络连接')
        elif 'ssl' in err_str:
            hints.append('SSL 证书问题，可尝试 verify=False')

    if not hints:
        hints.append('检查 login 方法的 URL、请求方法、参数名和格式')

    return '; '.join(hints)


def _diagnose_method_error(error, method_name, kwargs):
    """诊断方法调用失败原因"""
    hints = []

    if isinstance(error, Exception):
        err_str = str(error).lower()

        if '401' in err_str or 'unauthorized' in err_str:
            hints.append('未认证，login 可能未成功或 token 过期')
        elif '403' in err_str or 'forbidden' in err_str:
            hints.append('无权限，可能需要额外的 CSRF token 或权限')
        elif '404' in err_str:
            hints.append('接口路径错误，检查 URL 拼接是否正确')
        elif '400' in err_str:
            hints.append('请求参数错误，检查参数名和格式')
        elif '405' in err_str:
            hints.append('HTTP 方法不允许，可能 GET/POST 用反了')
        elif '422' in err_str:
            hints.append('参数验证失败，检查必填参数是否缺失')
        elif '500' in err_str:
            hints.append('服务器错误，可能是请求格式不兼容')
        elif 'connectionerror' in err_str:
            hints.append('网络错误，检查 base_url 和网络连接')
        elif 'keyerror' in err_str or 'attributeerror' in err_str:
            hints.append('响应解析失败，检查 _request 方法对响应的处理')
        elif 'typeerror' in err_str:
            hints.append('参数类型错误，检查方法签名和调用参数')

    if not hints:
        hints.append('检查方法的 URL、参数、请求方法和响应处理')

    return '; '.join(hints)


def _safe_sample(data, max_depth=2, max_keys=10):
    """安全地截取响应样本（脱敏+截断）"""
    if not isinstance(data, dict):
        return str(data)[:200]

    if max_depth <= 0:
        return '{...}'

    sample = {}
    for i, (k, v) in enumerate(data.items()):
        if i >= max_keys:
            sample['...'] = '({} more keys)'.format(len(data) - max_keys)
            break
        if isinstance(v, dict):
            sample[k] = _safe_sample(v, max_depth - 1, max_keys=5)
        elif isinstance(v, list):
            sample[k] = '[len={}]'.format(len(v))
        elif isinstance(v, str) and len(v) > 50:
            sample[k] = v[:50] + '...'
        else:
            sample[k] = v
    return sample


def _build_result(level, passed, checks, errors, **extra):
    """构建测试结果"""
    result = {
        'level': level,
        'passed': passed,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'checks': checks,
        'errors': errors,
        'summary': '{level} {status}: {pass_count}/{total} checks passed, {err} errors'.format(
            level=level,
            status='PASSED' if passed else 'FAILED',
            pass_count=sum(1 for c in checks if c['passed']),
            total=len(checks),
            err=len(errors),
        ),
    }
    result.update(extra)
    return result


# ============================================================
# 综合测试入口
# ============================================================

def run_tests(interface_path, site_config_path=None, site_id=None,
              base_url=None, credentials=None, test_plan=None,
              levels=None, base_dir=None):
    """
    运行指定级别的测试。

    Args:
        interface_path: interface.py 文件路径
        site_config_path: site_config.json 文件路径
        site_id: 站点标识
        base_url: 真实基础 URL（L3 需要）
        credentials: 登录凭证（L3 需要）
        test_plan: 测试计划（L3 可选）
        levels: 要运行的测试级别列表，如 ['L1', 'L2', 'L3']
        base_dir: skill 根目录

    Returns:
        dict: {
            'overall_passed': bool,
            'results': { 'L1': ..., 'L2': ..., 'L3': ... },
            'diagnosis_report': str,
            'fix_suggestions': [ ... ],
        }
    """
    if levels is None:
        levels = ['L1', 'L2']

    results = {}

    # L1
    if 'L1' in levels:
        print('\n[L1] 静态检查...')
        results['L1'] = l1_static_check(interface_path, site_config_path)
        print('  {}'.format(results['L1']['summary']))
        for err in results['L1']['errors']:
            print('  [错误] {}'.format(err['message']))

        if not results['L1']['passed']:
            print('  [跳过] L1 未通过，跳过后续测试')
            return _build_overall_result(results)

    # L2
    if 'L2' in levels:
        print('\n[L2] Dry-Run 测试...')
        results['L2'] = l2_dry_run(interface_path, site_id, base_dir)
        print('  {}'.format(results['L2']['summary']))
        for err in results['L2']['errors']:
            print('  [错误] {}'.format(err['message']))

        if not results['L2']['passed']:
            print('  [跳过] L2 未通过，跳过 L3 测试')
            return _build_overall_result(results)

    # L3
    if 'L3' in levels:
        if not base_url or not credentials:
            print('\n[L3] 跳过：需要提供 --base-url 和 --credentials')
        else:
            print('\n[L3] Live-Test...')
            results['L3'] = l3_live_test(
                interface_path, site_id, base_url, credentials,
                test_plan=test_plan, base_dir=base_dir,
            )
            print('  {}'.format(results['L3']['summary']))
            for err in results['L3']['errors']:
                print('  [错误] {}'.format(err['message']))

    return _build_overall_result(results)


def _build_overall_result(results):
    """构建综合测试结果"""
    all_errors = []
    for level, result in results.items():
        if result and not result['passed']:
            for err in result.get('errors', []):
                err['level'] = level
                all_errors.append(err)

    overall_passed = all(r['passed'] for r in results.values() if r)

    # 生成修复建议
    fix_suggestions = []
    for err in all_errors:
        suggestion = {
            'level': err.get('level', '?'),
            'type': err.get('type', 'unknown'),
            'message': err.get('message', ''),
            'fix_hint': err.get('fix_hint', ''),
        }
        if err.get('method'):
            suggestion['method'] = err['method']
        fix_suggestions.append(suggestion)

    return {
        'overall_passed': overall_passed,
        'results': results,
        'error_count': len(all_errors),
        'fix_suggestions': fix_suggestions,
    }


def generate_diagnosis_report(test_result, output_path=None):
    """
    生成结构化诊断报告（JSON 格式），供 AGENT 分析修复。

    Args:
        test_result: run_tests() 的返回值
        output_path: 输出路径（可选）

    Returns:
        str: JSON 格式的诊断报告
    """
    report = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'overall_passed': test_result['overall_passed'],
        'error_count': test_result['error_count'],
        'fix_suggestions': test_result['fix_suggestions'],
        'detail': {},
    }

    for level, result in test_result['results'].items():
        if result is None:
            continue
        report['detail'][level] = {
            'passed': result['passed'],
            'summary': result['summary'],
            'errors': result.get('errors', []),
        }
        # L2 额外信息
        if level == 'L2' and result.get('captured_requests'):
            report['detail'][level]['captured_requests'] = result['captured_requests']
        # L3 额外信息
        if level == 'L3' and result.get('responses'):
            report['detail'][level]['responses'] = result['responses']

    report_json = json.dumps(report, ensure_ascii=False, indent=2)

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_json)

    return report_json


def main():
    import argparse

    parser = argparse.ArgumentParser(description='接口测试器 - 三级闭环测试')
    parser.add_argument('interface_path', help='interface.py 文件路径')
    parser.add_argument('--site-config', help='site_config.json 文件路径')
    parser.add_argument('--site-id', help='站点标识')
    parser.add_argument('--base-url', help='真实基础 URL（L3 需要）')
    parser.add_argument('--username', help='登录用户名（L3 需要）')
    parser.add_argument('--password', help='登录密码（L3 需要）')
    parser.add_argument('--test-plan', help='测试计划 JSON 文件路径')
    parser.add_argument('--levels', default='L1,L2',
                        help='测试级别，逗号分隔（默认 L1,L2）')
    parser.add_argument('--report', help='诊断报告输出路径')
    parser.add_argument('--base-dir', help='skill 根目录')
    args = parser.parse_args()

    levels = [l.strip() for l in args.levels.split(',') if l.strip()]

    credentials = None
    if args.username and args.password:
        credentials = {'username': args.username, 'password': args.password}

    test_plan = None
    if args.test_plan and os.path.exists(args.test_plan):
        with open(args.test_plan, 'r', encoding='utf-8') as f:
            test_plan = json.load(f)

    result = run_tests(
        interface_path=args.interface_path,
        site_config_path=args.site_config,
        site_id=args.site_id,
        base_url=args.base_url,
        credentials=credentials,
        test_plan=test_plan,
        levels=levels,
        base_dir=args.base_dir,
    )

    # 输出诊断报告
    report = generate_diagnosis_report(result, args.report)

    if not result['overall_passed']:
        print('\n' + '=' * 50)
        print('诊断报告:')
        print('=' * 50)
        for suggestion in result['fix_suggestions']:
            print('\n  [{}] {} - {}'.format(
                suggestion['level'], suggestion['type'], suggestion['message']))
            print('  修复建议: {}'.format(suggestion['fix_hint']))
            if suggestion.get('method'):
                print('  涉及方法: {}'.format(suggestion['method']))

        if args.report:
            print('\n完整报告已保存: {}'.format(args.report))

        sys.exit(1)
    else:
        print('\n所有测试通过！')


if __name__ == '__main__':
    main()
