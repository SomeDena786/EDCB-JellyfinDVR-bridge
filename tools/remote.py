"""target PC への SSH 実行 / SFTP 転送補助スクリプト。

接続情報は環境変数から取得する (資格情報をファイルに残さないため):
    JFDVR_SSH_HOST   接続先ホスト
    JFDVR_SSH_USER   ユーザー名
    JFDVR_SSH_PASS   パスワード
    JFDVR_SSH_PORT   ポート (省略時 22)

サブコマンド:
    exec "<command>"        リモートでコマンドを実行し stdout/stderr/終了コードを表示
    put  <local> <remote>   ファイルを1つ転送する
    get  <remote> <local>   ファイルを1つ取得する
"""

from __future__ import annotations

import os
import sys

import paramiko

# 端末/パイプに関わらず UTF-8 で出力する
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _decode(data: bytes) -> str:
    for encoding in ('utf-8', 'cp932'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'replace')


def _connect() -> paramiko.SSHClient:
    host = os.environ['JFDVR_SSH_HOST']
    user = os.environ['JFDVR_SSH_USER']
    password = os.environ['JFDVR_SSH_PASS']
    port = int(os.environ.get('JFDVR_SSH_PORT', '22'))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, password=password, timeout=25)
    return client


def cmd_exec(command: str) -> int:
    client = _connect()
    try:
        _, stdout, stderr = client.exec_command(command, timeout=900)
        out = _decode(stdout.read())
        err = _decode(stderr.read())
        code = stdout.channel.recv_exit_status()
        if out:
            sys.stdout.write(out)
            if not out.endswith('\n'):
                sys.stdout.write('\n')
        if err.strip():
            sys.stdout.write('--- stderr ---\n')
            sys.stdout.write(err)
            if not err.endswith('\n'):
                sys.stdout.write('\n')
        sys.stdout.write(f'--- exit code: {code} ---\n')
        return code
    finally:
        client.close()


def cmd_put(local: str, remote: str) -> int:
    client = _connect()
    try:
        sftp = client.open_sftp()
        sftp.put(local, remote)
        sftp.close()
        print(f'put: {local} -> {remote}')
        return 0
    finally:
        client.close()


def cmd_get(remote: str, local: str) -> int:
    client = _connect()
    try:
        sftp = client.open_sftp()
        sftp.get(remote, local)
        sftp.close()
        print(f'get: {remote} -> {local}')
        return 0
    finally:
        client.close()


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    sub = args[0]
    if sub == 'exec' and len(args) == 2:
        return cmd_exec(args[1])
    if sub == 'put' and len(args) == 3:
        return cmd_put(args[1], args[2])
    if sub == 'get' and len(args) == 3:
        return cmd_get(args[1], args[2])
    print(__doc__)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
