#!/usr/bin/env python3
"""前任 AI 网页聊天 — 本地服务器
读取 ex-skill 生成的 persona，通过网页聊天界面对话。
手机连同一 WiFi 即可访问。
"""

import json
import os
import socket
import sys
import urllib.request
import urllib.error
from pathlib import Path
from flask import Flask, request, jsonify

# 设置 stdout 为 UTF-8（Windows 兼容）
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

app = Flask(__name__, static_folder='static', static_url_path='')

# ── 读取 Claude Code 配置获取 API 信息 ──────────────────────
def _load_api_config():
    settings_path = Path.home() / '.claude' / 'settings.json'
    if settings_path.exists():
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            env = settings.get('env', {})
            return (
                env.get('ANTHROPIC_AUTH_TOKEN', ''),
                env.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
                env.get('ANTHROPIC_DEFAULT_OPUS_MODEL_NAME', 'claude-sonnet-4-6'),
            )
        except Exception:
            pass
    return (
        os.environ.get('ANTHROPIC_AUTH_TOKEN', ''),
        os.environ.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
        os.environ.get('ANTHROPIC_DEFAULT_OPUS_MODEL_NAME', 'claude-sonnet-4-6'),
    )

API_KEY, BASE_URL, MODEL = _load_api_config()

# ── Exes 目录（优先当前目录，其次家目录） ──────────────────
DEFAULT_EXES = Path.cwd() / 'exes'
HOME_EXES = Path.home() / 'exes'


def find_exes_dir():
    if DEFAULT_EXES.exists():
        return DEFAULT_EXES
    return HOME_EXES


# ── 路由 ──────────────────────────────────────────────────


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/config')
def get_config():
    """返回配置信息"""
    exes_dir = find_exes_dir()
    return jsonify({
        'api_configured': bool(API_KEY),
        'exes_dir': str(exes_dir),
    })


@app.route('/api/exes')
def list_exes():
    """列出所有已创建的前任"""
    exes_dir = find_exes_dir()
    exes = []
    if exes_dir.exists():
        for d in sorted(exes_dir.iterdir()):
            if not d.is_dir() or d.name.startswith('.'):
                continue
            persona_path = d / 'persona.md'
            if not persona_path.exists():
                continue

            item = {'slug': d.name, 'name': d.name, 'profile': {}}
            meta_path = d / 'meta.json'
            if meta_path.exists():
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    item['name'] = meta.get('name', d.name)
                    profile = meta.get('profile', {})
                    item['profile'] = {
                        'occupation': profile.get('occupation', ''),
                        'city': profile.get('city', ''),
                        'mbti': profile.get('mbti', ''),
                        'zodiac': profile.get('zodiac', ''),
                    }
                except Exception:
                    pass
            exes.append(item)

    return jsonify(exes)


@app.route('/api/chat', methods=['POST'])
def chat():
    """与前任对话"""
    if not API_KEY:
        return jsonify({'error': '未配置 API Key，请检查 ~/.claude/settings.json'}), 500

    data = request.get_json()
    slug = data.get('slug', '')
    message = data.get('message', '').strip()
    history = data.get('history', [])

    if not slug or not message:
        return jsonify({'error': '缺少参数'}), 400

    # 读取 persona + memory
    exes_dir = find_exes_dir()
    persona_path = exes_dir / slug / 'persona.md'
    memory_path = exes_dir / slug / 'memory.md'

    system_parts = []

    if persona_path.exists():
        system_parts.append(persona_path.read_text(encoding='utf-8'))

    if memory_path.exists():
        system_parts.append(
            '\n---\n## 共同记忆\n\n' + memory_path.read_text(encoding='utf-8')
        )

    if not system_parts:
        return jsonify({
            'error': f'未找到「{slug}」的 persona 文件。\n请先在 Claude Code 中运行 /create-ex 创建。'
        }), 404

    system_parts.append(
        '\n\n---\n## 对话规则\n'
        '请完全以上述 persona 的身份回复。你就是这个人，不是 AI 助手。\n'
        '保持 ta 的棱角、说话风格和真实感。回复要简短自然，像真人聊天。\n'
        '用 ta 的语气词、口头禅、标点习惯。不要突然变得完美或温柔。'
    )
    system_prompt = '\n'.join(system_parts)

    # 构建消息历史（最近 30 条）
    messages = []
    for h in history[-30:]:
        role = 'assistant' if h['role'] == 'ex' else 'user'
        messages.append({'role': role, 'content': h['content']})
    messages.append({'role': 'user', 'content': message})

    # 调用 Anthropic 兼容 API
    api_url = f"{BASE_URL}/v1/messages"
    req_body = json.dumps({
        'model': MODEL,
        'max_tokens': 1024,
        'system': system_prompt,
        'messages': messages,
    }).encode('utf-8')

    req_headers = {
        'x-api-key': API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }

    try:
        req = urllib.request.Request(
            api_url, data=req_body, headers=req_headers, method='POST'
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode('utf-8'))

        content = result.get('content', [])
        reply = ''
        for block in content:
            if block.get('type') == 'text':
                reply += block.get('text', '')

        if not reply:
            return jsonify({'error': 'API 返回为空，请重试'}), 500

        return jsonify({'reply': reply})

    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8')[:500]
        except Exception:
            err_body = str(e)
        return jsonify({'error': f'API 错误 ({e.code}): {err_body}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)[:300]}), 500


# ── 启动 ──────────────────────────────────────────────────

if __name__ == '__main__':
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()

    print()
    print('  ==========================================')
    print('       Ex-Chat: QianRen AI Liao Tian')
    print('  ==========================================')
    print(f'  Local:   http://localhost:5899')
    print(f'  Network: http://{local_ip}:5899')
    print()
    print('  Make sure phone and PC are on same WiFi')
    print('  ==========================================')
    print()
    print(f'  API: {BASE_URL}')
    print(f'  Model: {MODEL}')
    print(f'  Exes dir: {find_exes_dir()}')
    print()

    app.run(host='0.0.0.0', port=5899, debug=False)
