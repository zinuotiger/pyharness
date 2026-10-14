"""Trace all three open-ended import sites using only synthetic modules."""
import importlib
import sys
from pyharness.bus.plugin import PluginManager
from pyharness.core.plugin_loader import load_spec


def test_manifest_import_and_file_execution(tmp_path, monkeypatch):
    entry=tmp_path/'audit_synthetic_capabilities.py'
    entry.write_text('capabilities = ["synthetic-capability"]\n',encoding='utf-8')
    monkeypatch.syspath_prepend(str(tmp_path))
    seen=[]
    original=importlib.import_module
    def traced(name,*args,**kwargs):
        seen.append(('import',name))
        return original(name,*args,**kwargs)
    monkeypatch.setattr(importlib,'import_module',traced)
    try:
        assert PluginManager()._load_caps({'entry':'audit_synthetic_capabilities'})==['synthetic-capability']
        package=tmp_path/'synthetic_plugin';package.mkdir()
        (package/'plugin.py').write_text('MANIFEST={"id":"synthetic_plugin"}\nexecuted=True\n',encoding='utf-8')
        original_spec=importlib.util.spec_from_file_location
        def spec(name,path,*args,**kwargs):
            seen.append(('spec',str(path)))
            result=original_spec(name,path,*args,**kwargs)
            execute=result.loader.exec_module
            def exec_module(module):
                seen.append(('execute',module.__name__))
                return execute(module)
            result.loader.exec_module=exec_module
            return result
        monkeypatch.setattr(importlib.util,'spec_from_file_location',spec)
        loaded=load_spec(package)
        assert loaded.module.executed
        assert [kind for kind,_ in seen]==['import','spec','execute']
    finally:
        sys.modules.pop('audit_synthetic_capabilities',None)
        if 'loaded' in locals(): sys.modules.pop(loaded.module.__name__,None)
