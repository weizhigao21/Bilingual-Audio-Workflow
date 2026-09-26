"""TTS 输出目录的生成配置标识。"""
import hashlib
import json
import os
import tempfile

MANIFEST = '.tts_profile.json'


def profile_key(config):
    mode = config.get('tts_mode', 'edge')
    if mode == 'edge':
        relevant = {
            'mode': mode,
            'voice': config.get('edge_voice', 'zh-CN-XiaoxiaoNeural'),
            'rate': config.get('edge_rate', '+0%'),
            'volume': config.get('edge_volume', '+0%'),
        }
    else:
        apis = config.get('api_configs', [])
        if config.get('use_multi_api', False):
            selected = [a for a in apis if a.get('status') == 'success'] or apis[:1]
        else:
            idx = config.get('current_api_index', 0)
            selected = [apis[idx]] if 0 <= idx < len(apis) else apis[:1]
        relevant = {
            'mode': mode,
            'apis': [(a.get('url', '').rstrip('/'), a.get('model', '')) for a in selected],
            'bulk': bool(config.get('use_bulk_api', True)),
        }
    data = json.dumps(relevant, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def profile_matches(directory, config):
    try:
        with open(os.path.join(directory, MANIFEST), encoding='utf-8') as f:
            return json.load(f).get('profile') == profile_key(config)
    except (OSError, ValueError, AttributeError):
        return False


def save_profile(directory, config):
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='.profile-', suffix='.json', delete=False) as f:
            path = f.name
            json.dump({'profile': profile_key(config)}, f)
        os.replace(path, os.path.join(directory, MANIFEST))
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
