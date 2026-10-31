"""Build Windows x64 portable ZIP and per-user NSIS installer from clean inputs."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
import zipfile
import requests

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / 'desktop'
VERSION = '1.2.0'
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
SDK_VERSION = '1.0.4258.31'
SDK_URL = f'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{SDK_VERSION}/microsoft.web.webview2.{SDK_VERSION}.nupkg'
SDK_SHA = '56f7f4b8bf9aee4b8efefbddd4f67d5f74ebd1b100ed0806da71bf76af481aa9'
NSIS_URL = 'https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.13/nsis-3.13.zip'
NSIS_SHA = 'ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8'

def run(args, cwd=ROOT):
    subprocess.run([str(x) for x in args], cwd=cwd, check=True, creationflags=FLAGS)

def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def fresh(path, owner):
    path, owner = path.resolve(), owner.resolve()
    if path == owner or owner not in path.parents:
        raise ValueError('Build deletion must stay inside the dedicated build root')
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)

def download(url, digest, cache, label):
    archive = cache / (label + '.zip')
    target = cache / label
    if target.is_dir() and any(target.iterdir()) and (not archive.exists() or checksum(archive) == digest):
        return target
    if not archive.exists() or checksum(archive) != digest:
        temporary = archive.with_suffix('.partial')
        if urlsplit(url).scheme != 'https':
            raise ValueError('Build downloads require HTTPS')
        with requests.get(url, headers={'User-Agent': 'VoiceLoom-Build/1.1'}, stream=True, timeout=(15, 90)) as response, temporary.open('wb') as output:
            response.raise_for_status()
            if urlsplit(response.url).scheme != 'https':
                raise ValueError('Build download redirected away from HTTPS')
            for block in response.iter_content(1024 * 1024):
                output.write(block)
        if checksum(temporary) != digest:
            temporary.unlink()
            raise ValueError(label + ' SHA256 mismatch')
        temporary.replace(archive)
    if not target.exists():
        with zipfile.ZipFile(archive) as source:
            for name in source.namelist():
                resolved = (target / name).resolve()
                if target.resolve() not in resolved.parents and resolved != target.resolve():
                    raise ValueError('Archive path escapes build cache')
            source.extractall(target)
    return target

def icon(path):
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (256, 256), '#101b2a')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 247, 247), radius=55, fill='#17283b')
    for x, height in zip((55, 85, 115, 145, 175, 205), (45, 90, 148, 108, 66, 32)):
        draw.rounded_rectangle((x-7, 128-height//2, x+7, 128+height//2), radius=7, fill='#81dbc9')
    image.save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

def license_bundle(payload, sdk):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    pending = [Requirement(line) for line in (ROOT/'requirements.txt').read_text().splitlines() if line and not line.startswith('#')]
    distributions = {}
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        if name in distributions:
            continue
        distribution = metadata.distribution(requirement.name)
        distributions[name] = distribution
        pending += [Requirement(text) for text in distribution.requires or []]
    components = []
    notices = payload/'licenses'
    notices.mkdir()
    for name, distribution in sorted(distributions.items()):
        entry = {'name': name, 'version': distribution.version,
                 'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License', ''),
                 'homepage': distribution.metadata.get('Home-page', '')}
        components.append(entry)
        target = notices/name
        target.mkdir()
        (target/'METADATA.txt').write_text(distribution.read_text('METADATA') or json.dumps(entry), encoding='utf-8')
        for file in distribution.files or []:
            if any(word in file.name.lower() for word in ('license', 'copying', 'notice', 'authors')):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    shutil.copy2(source, target/(str(file).replace('/', '_').replace('\\', '_')))
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():
        shutil.copy2(python_license, notices/'Python-LICENSE.txt')
    shutil.copy2(sdk/'LICENSE.txt', notices/'WebView2-SDK-LICENSE.txt')
    for package in ['react', 'react-dom', 'scheduler', 'lucide-react']:
        location = ROOT/'frontend/node_modules'/package
        for source in location.glob('LICENSE*'):
            shutil.copy2(source, notices/(package+'-'+source.name))
    (payload/'SBOM.json').write_text(json.dumps({'application': 'Voice Loom', 'version': VERSION,
        'python': sys.version, 'components': components,
        'webview2_sdk': SDK_VERSION, 'frontend': json.loads((ROOT/'frontend/package-lock.json').read_text())}, ensure_ascii=False, indent=2), encoding='utf-8')
    (notices/'README.txt').write_text('第三方组件许可见各目录。WebView2 Runtime 与 .NET Framework 由微软单独分发。\nAI 模型和模型服务由用户独立部署，其权重及许可未纳入本包。\n', encoding='utf-8')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, default=DESKTOP/'.build')
    parser.add_argument('--output', type=Path, default=ROOT/'release')
    parser.add_argument('--service-dir', type=Path, help='Reuse an already built PyInstaller directory during local packaging')
    parser.add_argument('--sdk-dir', type=Path)
    parser.add_argument('--nsis-dir', type=Path)
    parser.add_argument('--skip-frontend', action='store_true')
    args = parser.parse_args()
    work, output = args.work.resolve(), args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    cache = DESKTOP/'.cache'
    cache.mkdir(exist_ok=True)
    sdk = args.sdk_dir.resolve() if args.sdk_dir else download(SDK_URL, SDK_SHA, cache, 'webview2-'+SDK_VERSION)
    nsis = args.nsis_dir.resolve() if args.nsis_dir else download(NSIS_URL, NSIS_SHA, cache, 'nsis-3.13')/'nsis-3.13'
    if not args.skip_frontend:
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        if not npm:
            raise RuntimeError('Install Node.js LTS with npm before building')
        run([npm, 'ci'], ROOT/'frontend')
        run([npm, 'run', 'build'], ROOT/'frontend')
    if args.service_dir:
        service = args.service_dir.resolve()
    else:
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', work/'dist', '--workpath', work/'pyi', DESKTOP/'VoiceLoom.Service.spec'])
        service = work/'dist/VoiceLoom.Service'
    payload = work/'payload'
    fresh(payload, work)
    shutil.copytree(service, payload/'service')
    app_icon = work/'VoiceLoom.ico'
    icon(app_icon)
    arguments = ['/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/utf8output',
        '/out:"'+str(payload/'声织.exe')+'"', '/win32manifest:"'+str(DESKTOP/'app.manifest')+'"', '/win32icon:"'+str(app_icon)+'"']
    arguments += ['/reference:'+x for x in ['System.dll', 'System.Core.dll', 'System.Drawing.dll', 'System.Windows.Forms.dll', 'System.Web.Extensions.dll', 'System.Net.Http.dll', 'System.Security.dll']]
    lib = sdk/'lib/net462'
    arguments += ['/reference:"'+str(lib/name)+'"' for name in ['Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll']]
    arguments += ['"'+str(DESKTOP/'VoiceLoom.cs')+'"']
    response = work/'host.rsp'
    response.write_text('\n'.join(arguments), encoding='utf-8-sig')
    run([Path(os.environ.get('WINDIR', 'C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe', '@'+str(response)])
    shutil.copy2(DESKTOP/'app.config', payload/'声织.exe.config')
    for name in ['Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll']:
        shutil.copy2(lib/name, payload/name)
    shutil.copy2(sdk/'runtimes/win-x64/native/WebView2Loader.dll', payload/'WebView2Loader.dll')
    shutil.copy2(DESKTOP/'使用说明.txt', payload/'使用说明.txt')
    license_bundle(payload, sdk)
    # Every shipped path is enumerated, so uninstall only removes files from this build.
    manifest = work/'uninstall-files.nsh'
    files = sorted(path.relative_to(payload) for path in payload.rglob('*') if path.is_file())
    directories = sorted((path.relative_to(payload) for path in payload.rglob('*') if path.is_dir()), key=lambda path: len(path.parts), reverse=True)
    lines = ['Delete "$INSTDIR\\'+str(path).replace('/', '\\')+'"' for path in files]
    lines += ['Delete "$INSTDIR\\Uninstall.exe"']
    lines += ['RMDir "$INSTDIR\\'+str(path).replace('/', '\\')+'"' for path in directories]
    lines += ['RMDir "$INSTDIR"']
    manifest.write_text('\n'.join(lines), encoding='utf-8-sig')
    installer = output/f'VoiceLoom-{VERSION}-Setup-x64.exe'
    run([nsis/'makensis.exe', '/INPUTCHARSET', 'UTF8', '/DPAYLOAD='+str(payload), '/DOUTFILE='+str(installer), '/DUNINSTALL_FILES='+str(manifest), '/DAPPICON='+str(app_icon), DESKTOP/'installer.nsi'])
    portable = work/'VoiceLoom-Portable'
    fresh(portable, work)
    shutil.copytree(payload, portable, dirs_exist_ok=True)
    (portable/'portable.mode').write_text('Voice Loom portable mode\n', encoding='ascii')
    for old_artifact in output.glob('VoiceLoom-*'):
        if old_artifact.is_file() and f'VoiceLoom-{VERSION}-' not in old_artifact.name:
            old_artifact.unlink()
    extracted_portable = output/'VoiceLoom-Portable'
    fresh(extracted_portable, output)
    shutil.copytree(portable, extracted_portable, dirs_exist_ok=True)
    archive = output/f'VoiceLoom-{VERSION}-Portable-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(portable.rglob('*')):
            if path.is_file():
                bundle.write(path, str(Path(portable.name)/path.relative_to(portable)))
    artifacts = [archive, installer]
    (output/'SHA256SUMS.txt').write_text('\n'.join(checksum(path)+'  '+path.name for path in artifacts)+'\n', encoding='ascii')
    print(json.dumps({'portable': str(archive), 'installer': str(installer), 'local_app': str(extracted_portable/'声织.exe')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
