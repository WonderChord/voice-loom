from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
root = Path(SPECPATH).parent
datas = [(str(root / 'frontend/dist/index.html'), 'frontend/dist'),
         (str(root / 'frontend/dist/assets'), 'frontend/dist/assets'),
         (str(root / 'backend/sensevoice_worker.py'), 'backend')]
datas += collect_data_files('mcp') + collect_data_files('mcp_types') + collect_data_files('jsonschema_specifications')
datas += copy_metadata('mcp') + copy_metadata('mcp-types')
a = Analysis([str(root / 'desktop/service.py')], pathex=[str(root)],
             datas=datas, binaries=[],
             hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
                            'uvicorn.lifespan.on', 'backend.app', 'backend.sensevoice_client', 'backend.mcp_server'],
             hookspath=[str(root/'desktop/hooks')], hooksconfig={}, runtime_hooks=[],
             excludes=['torch','torchaudio','funasr','tkinter','matplotlib','PyQt5',
                       'scipy','PIL','cryptography','sqlite3',
                       'pytest','pip','bandit','pip_audit','numpy.tests','scipy.tests'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='VoiceLoom.Service',
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='VoiceLoom.Service')
