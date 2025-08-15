"""
Prepare a script  to create and pass the socket to libvirt. Then use it in
the incremental backup feature.
"""
import socket
import libvirt
import os
import selinux
import sys
SOCKET_PATH = sys.argv[1]
VM_NAME = sys.argv[2]
FDGROUP = sys.argv[3]
if os.path.exists(SOCKET_PATH):
    os.unlink(SOCKET_PATH)
#selinux.setsockcreatecon_raw("system_u:object_r:svirt_t:s0") # type: ignore[attr-defined]
selinux.setsockcreatecon_raw("system_u:object_r:svirt_t:s0")
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(SOCKET_PATH)
fdlist = [s.fileno()]
#conn = libvirt.open() # type: ignore[attr-defined]
conn = libvirt.open()
dom = conn.lookupByName(VM_NAME)
dom.FDAssociate(FDGROUP, fdlist)
print("associated")
input()
