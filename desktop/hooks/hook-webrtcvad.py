"""The maintained binary distribution is named webrtcvad-wheels."""
from PyInstaller.utils.hooks import copy_metadata
datas = copy_metadata('webrtcvad-wheels')
hiddenimports = ['_webrtcvad']
