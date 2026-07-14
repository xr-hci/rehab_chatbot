"""一键启动手机分享。

在 VS Code 中打开本文件，点击右上角“运行 Python 文件”即可。
它会启动本地服务、HTTPS Tunnel，并自动打开二维码。
"""

import os
from pathlib import Path


share_script = Path(__file__).with_name("share.command")
os.execv("/bin/zsh", ["/bin/zsh", str(share_script)])
