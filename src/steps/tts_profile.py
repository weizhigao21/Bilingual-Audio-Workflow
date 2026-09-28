"""TTS 输出目录的生成配置标识与目录命名。

目录命名规则（v2.1.20 起）：
    workspace/<md5(字幕内容 + "|" + profile_key(config))[:8]>/

把配音配置签名并进目录名，不同模型/声音/API 会落到不同目录，避免换模型后
片段同名互相覆盖、残留片段被混音器一起收进音轨。旧的"只哈希字幕内容"命名
由 legacy_dir_name() 保留，仅用于把历史目录迁移到新命名。
"""
import hashlib
import json
import os
import tempfile

MANIFEST = '.tts_profile.json'


def _model_tag(api):
    """API 配置里的用户自填模型标签（同一 URL 换模型时用来区分）。"""
    return str(api.get('model_tag', '') or '').strip()


def _api_entry(api):
    """API 配置的身份元组。

    只有填了 model_tag 才把它算进去——未填时与旧版完全一致，
    既有目录的 profile 与既有缓存键都不会被改写。
    """
    entry = (str(api.get('url', '')).rstrip('/'), str(api.get('model', '')))
    tag = _model_tag(api)
    return entry + (tag,) if tag else entry


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
            'apis': [_api_entry(a) for a in selected],
            'bulk': bool(config.get('use_bulk_api', True)),
        }
    data = json.dumps(relevant, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def dir_name(subtitle_bytes, config):
    """语音输出目录名：md5(字幕内容 | 配音配置签名)[:8]。

    配置签名变化（换声音/模型/语速/API）会让目录名一起变，因此不会再出现
    "两个模型的同名片段写进同一个目录"。
    """
    digest = hashlib.md5()
    digest.update(subtitle_bytes)
    digest.update(b'|')
    digest.update(profile_key(config).encode('ascii'))
    return digest.hexdigest()[:8]


def legacy_dir_name(subtitle_bytes):
    """旧版目录名：只哈希字幕内容。仅用于查找并迁移历史目录。"""
    return hashlib.md5(subtitle_bytes).hexdigest()[:8]


def api_cache_model(api_url, model_name, model_tag=''):
    """API 音频缓存的模型标识。

    旧值等价于 f"{model_name}|{api_url}"；填了 model_tag 才追加，
    因此未填标签时既有缓存键不变（不会整批失效）。
    """
    base = f"{model_name}|{str(api_url).rstrip('/')}"
    tag = str(model_tag or '').strip()
    return f"{base}|{tag}" if tag else base


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
