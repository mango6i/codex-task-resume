"""Local stdio MCP server exposing OpenAI structured plugin settings. No network or inference."""
import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'skills/quota-resume/scripts'))
import state as core
import preferences
import local_catalog
import i18n
import monitor
import announcements
import desktop_bridge

VERSION = '1.0.0'
SETTINGS_CAP = {'readTool': 'settings.read', 'updateTool': 'settings.update'}
EDITOR_URI = 'ui://task-resume/settings-studio-20261018-announcements.html'
LAUNCHER_URI = 'ui://task-resume/settings-launcher-studio-20261018-announcements.html'
UI_FILES = {EDITOR_URI: 'settings.html', LAUNCHER_URI: 'launcher.html'}
PROPERTIES = {
    'threshold': {'type': 'number', 'title': '剩余额度低于（%）', 'minimum': 0.01, 'maximum': 99,
                  'description': '严格低于这个百分比时，记录正在运行的本机任务。默认 2%。'},
    'delayMinutes': {'type': 'number', 'title': '额度刷新后等待（分钟）', 'minimum': 1, 'maximum': 60,
                     'description': '默认 2 分钟。例如 19:13 刷新，目标约 19:15。'},
    'message': {'type': 'string', 'title': '自动发送内容', 'minLength': 1, 'maxLength': 12000,
                'description': '按保存的原文发送到对应的原任务对话，默认“继续任务”。'},
    'checkSeconds': {'type': 'integer', 'title': '检测最短间隔（秒）', 'minimum': 30, 'maximum': 3600,
                    'description': '插件服务连接期间定期查询，与任务事件共用节流；断开后停止。'},
    'announcementCheckMinutes': {'type': 'integer', 'title': '公告检查间隔（分钟）', 'minimum': 1, 'maximum': 60,
                                'description': '只读检查 Tibo 的新原创帖，关键词 reset，不使用模型轮询。'},
    'announcementMessage': {'type': 'string', 'title': '公告触发后发送的检查指令', 'minLength': 1, 'maxLength': 12000,
                            'description': '发送到全部符合条件的本地根对话，使用 GPT-6 Astra 极高。保留原文和换行。'},
    'monitoringEnabled': {'type': 'boolean', 'title': '开启自动续接',
                          'description': '需先初始化控制对话，并在本地客户端信任检测脚本。关闭后立即停止后续发送。'},
    'announcementEnabled': {'type': 'boolean', 'title': '开启 Tibo 公告检查与重置停任务',
                            'description': '新 reset 帖触发项目检查；检测到两项额度恢复 100% 时停止运行中的本地项目。必须通过原生控制接口检查，默认关闭。'}
}


def read_settings(path):
    return {'schema': {'type': 'object', 'properties': PROPERTIES},
            'values': preferences.values(path),
            'layout': [
                {'kind': 'group', 'title': '统一管理', 'items': [
                    {'kind': 'tool', 'tool': 'settings.editor',
                     'description': '点击管理参数、勾选本地对话和查看排程。全局保存，不需要逐个对话发指令。'}]},
                {'kind': 'group', 'title': '触发条件', 'items': [
                    {'kind': 'property', 'property': 'threshold'},
                    {'kind': 'property', 'property': 'delayMinutes'}]},
                {'kind': 'group', 'title': '续接内容', 'items': [
                    {'kind': 'property', 'property': 'message'}]},
                {'kind': 'group', 'title': 'Tibo 重置公告', 'items': [
                    {'kind': 'property', 'property': 'announcementCheckMinutes'},
                    {'kind': 'property', 'property': 'announcementMessage'}]},
                {'kind': 'group', 'title': '运行控制', 'items': [
                    {'kind': 'property', 'property': 'checkSeconds'},
                    {'kind': 'property', 'property': 'monitoringEnabled'},
                    {'kind': 'property', 'property': 'announcementEnabled'}]}]}


def tools():
    return [
        {'name': 'announcements.status', 'title': '读取公告监控状态',
         'description': '只读读取公告、去重记录、原生控制接口和全量重置停任务状态。',
         'inputSchema': {'type': 'object', 'additionalProperties': False},
         'outputSchema': {'type': 'object', 'properties': {}},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False}},
        {'name': 'announcements.check', 'title': '检查公告与客户端兼容性',
         'description': '读取 Tibo 原创帖并核实本机控制通道，不启用功能、不发送消息、不停止任务。',
         'inputSchema': {'type': 'object', 'additionalProperties': False},
         'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': True}},
        {'name': 'settings.read', 'title': '读取额度恢复续接设置',
         'description': '读取设置表单及当前参数，不创建监控或发送消息。',
         'inputSchema': {'type': 'object', 'additionalProperties': False},
         'outputSchema': {'type': 'object', 'properties': {'schema': {'type': 'object'},
             'values': {'type': 'object'}, 'layout': {'type': 'array', 'items': {'type': 'object'}}},
             'required': ['schema', 'values']},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False}},
        {'name': 'settings.update', 'title': '保存额度恢复续接设置',
         'description': '只修改用户提交的设置。仅显式开启监控开关时启用自动续接。',
         'inputSchema': {'type': 'object', 'properties': {'set': {'type': 'object',
             'properties': PROPERTIES, 'minProperties': 1, 'additionalProperties': False}},
             'required': ['set'], 'additionalProperties': False},
         'outputSchema': {'type': 'object', 'properties': {'values': {'type': 'object'}}, 'required': ['values']},
         'annotations': {'readOnlyHint': False, 'destructiveHint': False,
                         'idempotentHint': True, 'openWorldHint': False},
         '_meta': {'ui': {'visibility': ['app', 'model']}}},
        {'name': 'settings.editor', 'title': '管理 Codex 任务续接',
         'description': '打开客户端内的设置表单；从原生插件设置按钮打开时，由客户端以官方弹窗承载。打开不会修改参数或启用监控。',
         'inputSchema': {'type': 'object', 'additionalProperties': False},
         'outputSchema': {'type': 'object', 'properties': {'values': {'type': 'object'},
             'configured': {'type': 'boolean'}, 'version': {'type': 'string'}},
             'required': ['values', 'configured', 'version']},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False},
         '_meta': {'ui': {'resourceUri': EDITOR_URI, 'visibility': ['app', 'model']},
                   'openai/outputTemplate': EDITOR_URI,
                   'openai/widgetAccessible': True}},
        {'name': 'settings.manage', 'title': 'Codex 任务续接 · 管理',
         'description': '固定管理入口。统一修改参数、只读列出本地对话并手动勾选续接目标。打开不发送消息。',
         'inputSchema': {'type': 'object', 'additionalProperties': False},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False},
         '_meta': {'ui': {'resourceUri': EDITOR_URI, 'visibility': ['app']},
                   'openai/outputTemplate': EDITOR_URI,
                   'openai/ui': {'entrypoints': [{'type': 'global'}, {'type': 'settings'}]}}},
        {'name': 'quota.read', 'title': '读取五小时额度',
         'description': '只读读取实际五小时额度和刷新时间，不发送消息、不调用模型。',
         'inputSchema': {'type': 'object', 'additionalProperties': False},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False},
         '_meta': {'ui': {'visibility': ['app', 'model']}}},
        {'name': 'tasks.list', 'title': '读取本地对话与排程',
         'description': '只读列出本机根对话、全局选择和实际排程，不调用模型。独立读取连接不能证明桌面任务已停止。',
         'inputSchema': {'type': 'object', 'properties': {'cursor': {'type': 'string'}}, 'additionalProperties': False},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False},
         '_meta': {'ui': {'visibility': ['app', 'model']}}},
        {'name': 'tasks.select', 'title': '保存全局续接目标',
         'description': '仅保存用户明确勾选的本地对话和范围。低额度时记录目标；不启用监控、不发送消息、不伪造排程成功。',
         'inputSchema': {'type': 'object', 'properties': {'mode': {'type': 'string', 'enum': ['automatic', 'selected']},
             'threadIds': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 200, 'uniqueItems': True}},
             'required': ['mode', 'threadIds'], 'additionalProperties': False},
         'annotations': {'readOnlyHint': False, 'idempotentHint': True, 'destructiveHint': False, 'openWorldHint': False},
         '_meta': {'ui': {'visibility': ['app', 'model']}}},
        {'name': 'settings.open', 'title': '打开设置弹窗入口',
         'description': '显示客户端内的“打开设置弹窗”按钮。用户点击后请求 ChatGPT 原生弹窗，不启动外部窗口、不修改设置。',
         'inputSchema': {'type': 'object', 'additionalProperties': False},
         'outputSchema': {'type': 'object', 'properties': {'version': {'type': 'string'},
                          'summary': {'type': 'object'}, 'configured': {'type': 'boolean'}},
                          'required': ['version', 'summary', 'configured']},
         'annotations': {'readOnlyHint': True, 'openWorldHint': False},
         '_meta': {'ui': {'resourceUri': LAUNCHER_URI, 'visibility': ['app', 'model']},
                   'openai/outputTemplate': LAUNCHER_URI, 'openai/widgetAccessible': True}}
    ]


def dispatch_raw(method, params, path):
    if method == 'initialize':
        return {'protocolVersion': '2025-11-25',
                'serverInfo': {'name': 'Codex 任务续接设置', 'version': VERSION},
                'capabilities': {'tools': {}, 'resources': {}, 'experimental': {'openai/settings': SETTINGS_CAP},
                                  'extensions': {'openai/settings': SETTINGS_CAP}}}
    if method == 'server/discover':
        return {'resultType': 'complete', 'supportedVersions': ['2026-07-28'],
                '_meta': {'io.modelcontextprotocol/serverInfo': {'name': 'Codex 任务续接设置', 'version': VERSION}},
                'capabilities': {'tools': {}, 'resources': {}, 'extensions': {'openai/settings': SETTINGS_CAP}}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': tools()}
    if method == 'resources/list':
        return {'resources': [{'uri': uri, 'name': filename,
                               'mimeType': 'text/html;profile=mcp-app'} for uri, filename in UI_FILES.items()]}
    if method == 'resources/read':
        uri = params.get('uri')
        if uri not in UI_FILES:
            raise ValueError('Resource not found')
        return {'contents': [{'uri': uri, 'mimeType': 'text/html;profile=mcp-app',
            'text': i18n.render(UI_FILES[uri]),
            '_meta': {'ui': {'prefersBorder': uri != EDITOR_URI,
                            'csp': {'connectDomains': [], 'resourceDomains': []}},
                      'openai/ui': {'availableDisplayModes': ['inline', 'fullscreen'],
                                    'preferredDisplayMode': 'inline'},
                      'openai/widgetDescription': 'Codex 任务续接的客户端内设置表单。',
                      'openai/widgetPrefersBorder': uri != EDITOR_URI,
                      'openai/widgetCSP': {'connect_domains': [], 'resource_domains': [],
                                           'redirect_domains': ['https://github.com', 'https://x.com']}}}]}
    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments', {})
        try:
            if not isinstance(args, dict):
                raise ValueError('参数格式不正确')
            if name == 'settings.read' and not args:
                result = read_settings(path)
            elif name == 'announcements.status' and not args:
                result = announcements.status(core.load(path) if path.exists() else {'settings': {}})
            elif name == 'announcements.check' and not args:
                posts = announcements.fetch_posts()
                if path.exists() and announcements.enabled(core.load(path)):
                    result = announcements.tick(path, provider=lambda: posts, force=True)
                else:
                    result = announcements.status(core.load(path) if path.exists() else {'settings': {}})
                    result['previewPost'] = posts[0]
                    result['previewMatchCount'] = sum(bool(re.search(r'\breset\b', p['text'], re.I)) for p in posts)
                result['control'] = desktop_bridge.capability(path)
            elif name in {'settings.editor', 'settings.manage'} and not args:
                result = {'values': preferences.values(path), 'configured': path.exists(),
                          'version': VERSION}
            elif name == 'settings.open' and not args:
                current = preferences.values(path)
                result = {'version': VERSION, 'configured': path.exists(),
                          'summary': {k: current[k] for k in ['threshold', 'delayMinutes', 'monitoringEnabled']}}
            elif name == 'settings.update' and set(args) == {'set'}:
                if isinstance(args['set'], dict) and args['set'].get('announcementEnabled') is True:
                    control = desktop_bridge.capability(path)
                    if not control['available']:
                        raise ValueError(i18n.text(control['reason'])+i18n.text(' 新功能未启用；原有参数未修改。'))
                result = {'values': preferences.update(path, args['set'])}
            elif name == 'quota.read' and not args:
                result = local_catalog.read_quota(path)
            elif name == 'tasks.list' and not set(args)-{'cursor'}:
                if 'cursor' in args and not isinstance(args['cursor'], str):
                    raise ValueError('分页参数无效')
                result = local_catalog.list_threads(path, args.get('cursor'))
            elif name == 'tasks.select' and set(args) == {'mode', 'threadIds'}:
                result = local_catalog.update_selection(path, args['mode'], args['threadIds'])
            else:
                raise ValueError('不支持的工具或参数')
            _, saved_state = preferences.effective(path)
            configured = bool(saved_state and saved_state.get('accountId') and saved_state.get('controllerId'))
            if 'configured' in result:
                result['configured'] = configured
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
                    'structuredContent': result,
                    '_meta': {'skillhub/setup': {'configured': configured, 'hookTrust': 'not_checked'}}}
        except (ValueError, OSError, KeyError, TypeError) as error:
            return {'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}
    if method in {'resources/list', 'resources/templates/list', 'prompts/list'}:
        key = {'resources/list': 'resources', 'resources/templates/list': 'resourceTemplates',
               'prompts/list': 'prompts'}[method]
        return {key: []}
    raise ValueError('Method not found')


def dispatch(method, params, path):
    result = dispatch_raw(method, params, path)
    lang = i18n.language()
    if method in {'initialize', 'server/discover', 'tools/list'}:
        result = i18n.metadata(result, lang)
    if method == 'tools/call':
        result.setdefault('_meta', {})['task-resume/locale'] = lang
        if result.get('isError'):
            for item in result.get('content', []):
                if item.get('type') == 'text':
                    item['text'] = i18n.text(item['text'], lang)
        elif params.get('name') == 'settings.read':
            data = result['structuredContent']
            for key in ['schema', 'layout']:
                data[key] = i18n.metadata(data[key], lang)
            result['content'][0]['text'] = json.dumps(data, ensure_ascii=False)
    return result


def main(start_monitor=True):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    path = (args.state or core.data_dir()/'state.json').resolve()
    worker = monitor.Worker(path) if start_monitor else None
    if worker:
        worker.start()
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            if 'id' not in request:
                continue
            result = dispatch(request.get('method'), request.get('params', {}), path)
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as error:
            response = {'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) else None,
                        'error': {'code': -32601 if str(error) == 'Method not found' else -32602,
                                  'message': str(error)}}
        print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
    if worker:
        worker.close()


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    main()
