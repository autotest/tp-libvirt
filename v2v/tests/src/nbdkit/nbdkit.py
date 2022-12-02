import re
import os
import logging
import tempfile

from avocado.utils import process
from virttest import data_dir
from virttest import utils_misc
from virttest.utils_conn import build_server_key, build_CA
from virttest.utils_v2v import multiple_versions_compare
from virttest.utils_v2v import params_get

LOG = logging.getLogger('avocado.v2v.' + __name__)


def run(test, params, env):
    """
    Basic nbdkit tests
    """
    checkpoint = params.get('checkpoint')
    version_required = params.get('version_required')

    def test_filter_stats_fd_leak():
        """
        check if nbdkit-stats-filter leaks an fd
        """
        tmp_logfile = os.path.join(data_dir.get_tmp_dir(), "nbdkit-test.log")
        cmd = """
nbdkit -U - --filter=log --filter=stats sh - \
  logfile=/dev/null statsfile=/dev/null \
  --run 'qemu-io -r -f raw -c "r 0 1" $nbd' <<\EOF
  case $1 in
    get_size) echo 1m;;
    pread) ls -l /proc/$$/fd > %s
      dd iflag=count_bytes count=$3 if=/dev/zero || exit 1 ;;
    *) exit 2 ;;
  esac
EOF
""" % tmp_logfile

        process.run(cmd, shell=True)

        count = 0
        with open(tmp_logfile) as fd:
            ptn = r'(\d+)\s+->\s+\'(pipe|socket):'
            cont = fd.read()
            LOG.debug('all fds:\n%s', cont)
            for i, _ in re.findall(ptn, cont):
                if int(i) > 2:
                    count += 1
        if count > 0:
            test.fail('nbdkit-stats-filter leaks %d fd' % count)

    def test_has_run_againt_vddk7_0():
        """
        check if nbdkit --run + vddk + esx7.0 works.
        """
        from virttest.utils_pyvmomi import VSphereConnection, vim

        vm_name = params_get(params, "main_vm")
        if not vm_name:
            test.error('No VM specified')
        # vsphere server's host name or IP address
        vsphere_host = params_get(params, "vsphere_host")
        vsphere_user = params_get(params, "vsphere_user", 'root')
        # vsphere password
        vsphere_pwd = params_get(params, "vsphere_pwd")
        vsphere_passwd_file = params_get(
            params, "vpx_passwd_file", '/tmp/v2v_vpx_passwd')
        with open(vsphere_passwd_file, 'w') as fd:
            fd.write(vsphere_pwd)

        # get vm and file's value
        connect_args = {
            'host': vsphere_host,
            'user': vsphere_user,
            'pwd': vsphere_pwd}
        with VSphereConnection(**connect_args) as conn:
            conn.target_vm = vm_name
            nbdkit_vm_name = 'moref=' + \
                str(conn.target_vm).strip('\'').split(':')[1]
            nbdkit_file = conn.get_hardware_devices(
                dev_type=vim.vm.device.VirtualDisk)[0].backing.fileName

        # vddk_libdir
        vddk_libdir_src = params_get(params, "vddk_libdir_src")
        with tempfile.TemporaryDirectory(prefix='vddklib_') as vddk_libdir:
            utils_misc.mount(vddk_libdir_src, vddk_libdir, 'nfs')
            vddk_thumbprint = '11'
            nbdkit_cmd = """
nbdkit -rfv -U - --exportname / \
  --filter=cacheextents --filter=retry vddk server=%s user=%s password=+%s vm=%s \
  file='%s' libdir=%s --run 'qemu-img info $nbd' thumbprint=%s
""" % (vsphere_host, vsphere_user, vsphere_passwd_file, nbdkit_vm_name, nbdkit_file, vddk_libdir, vddk_thumbprint)
            # get thumbprint by a trick
            cmd_result = process.run(
                nbdkit_cmd, shell=True, ignore_status=True)
            output = cmd_result.stdout_text + cmd_result.stderr_text
            vddk_thumbprint = re.search(
                r'PeerThumbprint:\s+(.*)', output).group(1)

            # replace thumbprint with correct value
            nbdkit_cmd = nbdkit_cmd.strip()[:-2] + vddk_thumbprint
            LOG.info('nbdkit command:\n%s', nbdkit_cmd)

            if checkpoint == 'vddk_stats':
                vddk_stats = params_get(params, "vddk_stats")
                nbdkit_cmd = nbdkit_cmd + ' -D vddk.stats=%s' % vddk_stats
                LOG.info('nbdkit command with -D option:\n%s', nbdkit_cmd)
            if checkpoint == 'backend_datapath_controlpath':
                nbdkit_cmd = nbdkit_cmd + ' -D nbdkit.backend.datapath=0 -D nbdkit.backend.controlpath=0'
                LOG.info('nbdkit command with -D option:\n%s', nbdkit_cmd)

            # Run the final nbdkit command
            output = process.run(nbdkit_cmd, shell=True).stdout_text
            utils_misc.umount(vddk_libdir_src, vddk_libdir, 'nfs')
            if checkpoint == 'vddk_stats':
                if vddk_stats == 1 and not re.search(
                        r'VDDK function stats', output):
                    test.fail('failed to test vddk_stats')
                if vddk_stats == 0 and re.search(
                        r'VDDK function stats', output):
                    test.fail('failed to test vddk_stats')
            if checkpoint == 'has_run_againt_vddk7_0' and not re.search(
                    r'virtual size', output):
                test.fail('failed to test has_run_againt_vddk7_0')
            if checkpoint == 'backend_datapath_controlpath' and re.search(r'vddk: (open|pread)', output):
                test.fail('fail to test nbdkit.backend.datapath and nbdkit.backend.controlpath option')

    def test_memory_max_disk_size():
        """
        check case for bz1913740
        """
        if multiple_versions_compare('[qemu-kvm-5.2.0-3,)'):
            mem_size = "2**63 - 2**30"
            invalid_mem_size = "2**63 - 2**30 + 1"
        else:
            mem_size = "2**63 - 512"
            invalid_mem_size = "2**63 - 512 + 1"

        cmd = 'nbdkit memory $((%s)) --run \'qemu-img info "$uri"\'' % mem_size
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if cmd_result.exit_status != 0:
            test.fail('failed to test memory_max_disk_size')

        cmd = 'nbdkit memory $((%s)) --run \'qemu-img info "$uri"\'' % invalid_mem_size
        expected_msg = 'File too large'
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if cmd_result.exit_status == 0 or expected_msg in cmd_result.stdout_text:
            test.fail('failed to test memory_max_disk_size')

    def test_data_corruption():
        """
        check case for bz1990134
        """
        cmd = """nbdkit --filter=cow data "33 * 100000" --run 'nbdsh -u $uri -c "h.trim(100000, 0)" ; nbdcopy $uri - | hexdump -C'"""
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if cmd_result.exit_status != 0 or '21 21' in cmd_result.stdout_text:
            test.fail('failed to test data_corruption')

    def test_cve_2019_14850():
        """
        check case for bz1757263
        """
        cred_dict = {
            'cakey': 'ca-key.pem',
            'cacert': 'ca-cert.pem',
            'serverkey': 'server-key.pem',
            'servercert': 'server-cert.pem'}
        tls_dir = data_dir.get_tmp_dir()
        build_CA(tls_dir, credential_dict=cred_dict)
        build_server_key(
            tls_dir,
            credential_dict=cred_dict,
            server_ip='127.0.0.0')
        cmd1 = """echo | nbdkit -fv --tls=require --tls-certificates=/root null --run "nc localhost 10809" --tls-verify-peer"""
        cmd2 = "nbdkit -fv null --run 'sleep 1 >/dev/tcp/localhost/10809' 2>&1"
        for cmd in [cmd1, cmd2]:
            cmd_result = process.run(cmd, shell=True, ignore_status=True)
            if 'open readonly' in cmd_result.stdout_text:
                test.fail('failed to test cve_2019_14850')

    def test_python_error():
        """
        check case for bz1613946
        """
        lines = """
def function():
    raise RuntimeError("error")
def config(key, value):
    function()
def open(readonly):
    raise RuntimeError("open")
def get_size(h):
    raise RuntimeError("get_size")
def pread(h, count, offset):
    raise RuntimeError("pread")
    """

        python_file_path = os.path.join(data_dir.get_tmp_dir(), "python_check.py")
        with open(python_file_path, "w") as f:
            f.write(lines)
        cmd = "nbdkit -fv python %s foo=bar" % python_file_path
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if not re.search('config: error: Traceback', cmd_result.stderr_text):
            test.fail('failed to test enhance_python_error')

    def test_checkwrite_filter():
        lines = """
#!/bin/bash -

set -e

rm -f /tmp/sock

nbdkit --exit-with-parent -U /tmp/sock sh - --filter=checkwrite <<'EOF' &
case "$1" in
  get_size) echo 1048576 ;;
  pread) dd if=/dev/zero count=$3 iflag=count_bytes ;;
  can_extents) exit 0 ;;
  extents)
    echo 0 262144 3
    echo 262144 131072 0
    echo 393216 655360 3
  ;;
  *) exit 2 ;;
esac
EOF

sleep 1

nbdsh -u nbd+unix:///?socket=/tmp/sock -c 'h.zero (655360, 262144, 0)'
        """

        file_path = data_dir.get_tmp_dir()
        shell_file_path = os.path.join(file_path, "bound.sh")
        with open(shell_file_path, "w") as f:
            f.write(lines)
        cmd = "cd %s; chmod +x %s; ./bound.sh" % (file_path, shell_file_path)
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if re.search('core dumped', cmd_result.stderr_text):
            test.fail('failed to test checkwrite filter')

    def test_blocksize_policy_filter():
        cmd_1 = "nbdkit --filter=blocksize-policy memory 1G blocksize-preferred=32K blocksize-maximum=40K " \
                "blocksize-minimum=4K blocksize-error-policy=allow"
        cmd_2 = "nbdinfo nbd://localhost"
        cmd_1_result = process.run(cmd_1, shell=True, ignore_status=True)
        if re.search('error', cmd_1_result.stderr_text):
            test.fail('failed to execute nbdkit command with blocksize-policy filter')
        cmd_2_result = process.run(cmd_2, shell=True, ignore_status=True)
        for data in ['block_size_minimum: 4096', 'block_size_preferred: 32768', 'block_size_maximum: 40960']:
            if not re.search(data, cmd_2_result.stdout_text):
                test.fail('failed to test blocksize policy filter')

    if version_required and not multiple_versions_compare(
            version_required):
        test.cancel("Testing requires version: %s" % version_required)

    if checkpoint == 'filter_stats_fd_leak':
        test_filter_stats_fd_leak()
    elif checkpoint in ['has_run_againt_vddk7_0', 'vddk_stats', 'backend_datapath_controlpath']:
        test_has_run_againt_vddk7_0()
    elif checkpoint == 'memory_max_disk_size':
        test_memory_max_disk_size()
    elif checkpoint == 'data_corruption':
        test_data_corruption()
    elif checkpoint == 'cve_2019_14850':
        test_cve_2019_14850()
    elif checkpoint == 'enhance_python_error':
        test_python_error()
    elif checkpoint == 'checkwrite':
        test_checkwrite_filter()
    elif checkpoint == 'blocksize_policy':
        test_blocksize_policy_filter()
    else:
        test.error('Not found testcase: %s' % checkpoint)
