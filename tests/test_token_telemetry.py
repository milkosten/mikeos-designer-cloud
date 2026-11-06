import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from server import gpu


class TelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_provider_paths(self):
        for router in (True, False):
            for streaming in (True, False):
                with self.subTest(router=router, streaming=streaming):
                    reports = []

                    async def handle(request):
                        body = json.loads(request.content)
                        if 'telemetry-api' in str(request.url):
                            reports.append(body)
                            return httpx.Response(200)
                        if router:
                            data = {'model': 'kimi-test', 'usage': {'prompt_tokens': 9, 'completion_tokens': 4}}
                            if streaming:
                                self.assertEqual(body['stream_options'], {'include_usage': True})
                                text = 'data: ' + json.dumps({'choices': [{'delta': {'content': 'ok'}}]}) + '\n\n'
                                text += 'data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n'
                                return httpx.Response(200, text=text)
                            data['choices'] = [{'message': {'content': 'ok'}}]
                        else:
                            data = {'model': 'qwen-test', 'message': {'content': 'ok'}, 'done': True,
                                    'prompt_eval_count': 9, 'eval_count': 4}
                            if streaming:
                                return httpx.Response(200, text=json.dumps(data) + '\n')
                        return httpx.Response(200, json=data)

                    original = httpx.AsyncClient
                    def client(**kwargs):
                        return original(transport=httpx.MockTransport(handle), **kwargs)

                    with patch.dict(os.environ, {'SVC_TELEMETRY_KEY': 'test-only'}), \
                         patch.object(gpu, 'OPENROUTER_API_KEY', 'test-only' if router else ''), \
                         patch.object(gpu.httpx, 'AsyncClient', client):
                        if streaming:
                            result = ''.join([chunk async for chunk in gpu.stream_chat([])])
                        else:
                            result = await gpu.chat([])
                        self.assertEqual(result, 'ok')
                        await asyncio.gather(*gpu._telemetry_tasks)
                    self.assertEqual(len(reports), 1)
                    self.assertEqual(reports[0]['source_id'], 'designer')
                    self.assertEqual(reports[0]['usage']['input_tokens'], 9)
                    self.assertEqual(reports[0]['usage']['output_tokens'], 4)

    async def test_failure_does_not_delay_or_raise(self):
        started = asyncio.Event()
        release = asyncio.Event()
        async def send(*args):
            started.set()
            await release.wait()
        with patch.dict(os.environ, {'SVC_TELEMETRY_KEY': 'test-only'}), patch.object(gpu, '_send_tokens', send):
            gpu._report_tokens({'input_tokens': 9, 'output_tokens': 4, 'reasoning_output_tokens': 2})
            await started.wait()
            release.set()
            await asyncio.gather(*gpu._telemetry_tasks)
        with patch.object(gpu.httpx, 'AsyncClient', side_effect=RuntimeError('offline')):
            await gpu._send_tokens({'input_tokens': 9}, 'test', 'test-only')


if __name__ == '__main__':
    unittest.main()
