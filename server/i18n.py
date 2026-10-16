"""Windows display-language detection and self-contained localized MCP App resources."""
import ctypes
import base64
import json
import locale
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def language(name=None):
    if name is None:
        try:
            if os.name == 'nt':
                name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), 'en_US')
            else:
                name = locale.getlocale()[0] or 'en'
        except (AttributeError, OSError, ValueError):
            name = 'en'
    return 'zh-CN' if str(name).lower().replace('_', '-').startswith('zh') else 'en'


@lru_cache(maxsize=1)
def translations():
    return json.loads((ROOT/'ui/translations.json').read_text(encoding='utf-8'))


def text(value, lang=None):
    return translations().get(value, value) if (lang or language()) == 'en' else value


def metadata(value, lang=None):
    lang = lang or language()
    if isinstance(value, dict):
        return {k: metadata(v, lang) for k, v in value.items()}
    if isinstance(value, list):
        return [metadata(v, lang) for v in value]
    return text(value, lang) if isinstance(value, str) else value


def render(filename, lang=None):
    lang = lang or language()
    payload = json.dumps({'language': lang, 'messages': translations()}, ensure_ascii=False).replace('<', '\\u003c')
    bootstrap = """(()=>{const cfg=__CONFIG__;
const t=(key,vars={})=>{let value=cfg.language==='en'?(cfg.messages[key]??key):key;return value.replace(/\\{(\\w+)\\}/g,(_,k)=>String(vars[k]??'{'+k+'}'));};
document.documentElement.lang=cfg.language;
function staticText(root){const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;while(node=walk.nextNode()){if(node.parentElement?.closest('script,style,textarea'))continue;const raw=node.nodeValue,key=raw.trim();if(cfg.language==='en'&&cfg.messages[key])node.nodeValue=raw.replace(key,t(key));}for(const element of root.querySelectorAll('[aria-label],[title],[placeholder]'))for(const name of ['aria-label','title','placeholder'])if(element.hasAttribute(name))element.setAttribute(name,t(element.getAttribute(name)));}
window.TaskResumeI18n={t,language:cfg.language,staticText};})();""".replace('__CONFIG__', payload)
    icon = 'data:image/svg+xml;base64,'+base64.b64encode((ROOT/'assets/icon.svg').read_bytes()).decode('ascii')
    return (ROOT/'ui'/filename).read_text(encoding='utf-8').replace('/*__TASK_RESUME_I18N__*/', bootstrap).replace('__TASK_RESUME_ICON__', icon)


def localize_manifest(path, lang=None):
    if (lang or language()) != 'en':
        return
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    english = json.loads((ROOT/'ui/manifest-en.json').read_text(encoding='utf-8'))
    value['description'] = english['description']
    value['interface'].update(english['interface'])
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    localize_manifest(parser.parse_args().manifest)
