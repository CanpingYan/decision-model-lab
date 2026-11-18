#!/usr/bin/env python3
"""Decision Model Lab: local HTTP, imported Excel and five model adapters."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import ssl
import sys
import urllib.parse

from lab.config import load_settings
from lab.core import LabStore, MODEL_NAMES, run_case
from lab.providers import ProviderRegistry
from lab.workbook import import_workbook

WEB_ROOT = Path(__file__).resolve().parent / 'web'


def ensure_ca():
    if not ssl.get_default_verify_paths().cafile and not os.environ.get('SSL_CERT_FILE'):
        for path in ['/opt/homebrew/etc/ca-certificates/cert.pem', '/etc/ssl/cert.pem']:
            if Path(path).is_file():
                os.environ['SSL_CERT_FILE'] = path
                break


class LabServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, settings, registry=None, store=None):
        if Path(settings['LAB_DATA_DIR']).resolve().is_relative_to(WEB_ROOT.resolve()):
            raise ValueError('LAB_DATA_DIR 必须位于静态网页目录 app/web 之外')
        self.settings = settings
        self.registry = registry or ProviderRegistry(settings)
        self.store = store or LabStore(settings['LAB_DATA_DIR'])
        super().__init__(address, partial(Handler, directory=str(WEB_ROOT)))


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.js': 'text/javascript',
                      '.mjs': 'text/javascript',
                      '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}

    def log_message(self, format, *args):
        if len(args) > 1 and str(args[1]) not in ('200', '201', '304'):
            super().log_message(format, *args)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        super().end_headers()

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def trusted(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in allowed and (not origin or origin in {'http://' + host for host in allowed})

    def path_parts(self):
        return urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).strip('/').split('/')

    def read_body(self, maximum=500000):
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('请求体长度无效') from None
        if not 0 < size <= maximum:
            raise ValueError('请求内容为空或超出大小限制')
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError('上传未完成，请重新选择文件')
        return data

    def read_json(self):
        try:
            value = json.loads(self.read_body())
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('请求内容不是合法 JSON') from None
        if not isinstance(value, dict):
            raise ValueError('请求内容必须是对象')
        return value

    def guarded(self, action):
        if not self.trusted():
            return self.send_json({'error': '仅允许本机应用页面访问'}, 403)
        try:
            return action()
        except KeyError as error:
            return self.send_json({'error': str(error).strip("'")}, 404)
        except (ValueError, TypeError) as error:
            return self.send_json({'error': str(error)}, 400)
        except Exception as error:
            return self.send_json({'error': str(error)[:1500]}, 502)

    def do_GET(self):
        parts = self.path_parts()
        if parts[0] == 'api':
            return self.guarded(lambda: self.get_api(parts))
        if not self.trusted() or any(part.startswith('.') or part == '..' for part in parts if part):
            return self.send_json({'error': '文件不存在'}, 404)
        return super().do_GET()

    def list_directory(self, path):
        self.send_json({'error': '目录列表不可用'}, 404)
        return None

    def get_api(self, parts):
        if parts == ['api', 'status']:
            return self.send_json({'name': 'Decision Model Lab', 'models': self.server.registry.statuses(),
                                   **self.server.store.selection()})
        if parts == ['api', 'datasets']:
            return self.send_json({'datasets': self.server.store.list_datasets()})
        if len(parts) == 3 and parts[:2] == ['api', 'jobs']:
            return self.send_json({'job': self.server.store.get_job(parts[2])})
        if len(parts) == 3 and parts[:2] == ['api', 'datasets']:
            return self.send_json({'dataset': self.server.store.get_dataset(parts[2])})
        return self.send_json({'error': '接口不存在'}, 404)

    def do_POST(self):
        return self.guarded(lambda: self.post_api(self.path_parts()))

    def post_api(self, parts):
        store, registry = self.server.store, self.server.registry
        if parts == ['api', 'import']:
            name = Path(urllib.parse.unquote(self.headers.get('X-Filename', '测试用例.xlsx'))).name
            if not name.lower().endswith('.xlsx'):
                raise ValueError('请导入 .xlsx 格式的 Excel；旧版 .xls 请先另存为 .xlsx')
            dataset, warnings = import_workbook(self.read_body(20 * 1024 * 1024), name)
            with store.lock:
                store.save_dataset(dataset)
                selection = store.selection()
            return self.send_json({**selection, 'warnings': warnings})
        if parts == ['api', 'datasets', 'select']:
            body = self.read_json()
            with store.lock:
                store.select_dataset(body.get('datasetId'))
                selection = store.selection()
            return self.send_json(selection)
        if len(parts) == 4 and parts[:2] == ['api', 'models'] and parts[3] in ('load', 'unload'):
            model_id, operation = parts[2:]
            if model_id not in MODEL_NAMES:
                raise ValueError('未知模型')
            if any(j['state'] == 'running' for j in store.recent_jobs()):
                raise ValueError('批量测试正在运行，请先停止后再加载或卸载模型')
            status = getattr(registry, operation)(model_id)
            return self.send_json({'model': status, 'models': registry.statuses()})
        if parts == ['api', 'decide']:
            body = self.read_json()
            dataset = store.get_current_dataset(body.get('datasetId'))
            case = next((case for case in dataset['cases'] if case['id'] == body.get('caseId')), None)
            if not case:
                raise ValueError('用例不属于当前导入题集')
            if any(j['state'] == 'running' for j in store.recent_jobs()):
                raise ValueError('批量测试正在运行，请等待完成或停止后再运行演示')
            result = run_case(registry, case, body.get('model'))
            result['datasetId'] = dataset['id']
            return self.send_json(result)
        if parts == ['api', 'jobs']:
            body = self.read_json()
            return self.send_json({'job': store.start_job(registry, body.get('datasetId'),
                                                        body.get('models'), body.get('caseIds'))})
        if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'stop':
            return self.send_json({'job': store.stop_job(parts[2])})
        return self.send_json({'error': '接口不存在'}, 404)


def main():
    ensure_ca()
    settings = load_settings()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(settings['LAB_PORT'])
    server = LabServer(('127.0.0.1', port), settings)
    print(f'Decision Model Lab — http://127.0.0.1:{port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止决策模型实验室。', flush=True)
    finally:
        for job in server.store.recent_jobs():
            if job['state'] == 'running':
                server.store.stop_job(job['id'])
        server.registry.close()
        server.server_close()


if __name__ == '__main__':
    main()
