# -*- coding: utf-8 -*-
"""双语音声工作流主控包。"""


def _disable_broken_zstd_backport():
    """剔除残缺的 backports.zstd，避免 import requests 崩溃。

    urllib3 2.8 探测到 backports.zstd 存在就认为支持 zstd，并在模块级读取
    zstd.ZstdError；但打包环境里可能只收集到 _zstd 扩展、缺包体，
    结果 import requests 直接抛 AttributeError。本项目不需要 zstd 解压，
    这里让 urllib3 走"不支持 zstd"分支即可。
    """
    try:
        from backports import zstd as _zstd
    except Exception:
        return
    if hasattr(_zstd, 'ZstdError'):
        return
    import sys

    sys.modules['backports.zstd'] = None
    try:
        import backports

        delattr(backports, 'zstd')
    except Exception:
        pass


_disable_broken_zstd_backport()
