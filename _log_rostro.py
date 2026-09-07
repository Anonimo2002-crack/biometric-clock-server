import shlex

import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.1.16", username="ncoguox", password="admin98", timeout=20)
cmd = r"""
echo '=== journal rostro ==='
journalctl -u biometric-clock --since '10 min ago' --no-pager | tail -n 80
echo '=== ping relojes ==='
ping -c 1 -W 1 192.168.1.14 && echo OK14 || echo NO14
ping -c 1 -W 1 192.168.1.19 && echo OK19 || echo NO19
echo '=== env timeout ==='
grep -E 'DEVICE_|CAPTURA' /home/ncoguox/asistencia/biometric-clock-server/.env
"""
wrapped = f"echo admin98 | sudo -S -p '' bash -lc {shlex.quote(cmd)}"
_, o, e = c.exec_command(wrapped, timeout=30)
print((o.read() + e.read()).decode("utf-8", "replace").encode("ascii", "replace").decode("ascii"))
c.close()
